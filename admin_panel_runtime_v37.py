from __future__ import annotations

import argparse
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import admin_action_queue
import admin_bot as legacy
from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS
from admin_panel_runtime_v33 import WHEEL_NOTIFICATION_OPTIONS
from admin_panel_runtime_v36 import TelegramPanelRuntimeV36


UTC = timezone.utc
NOTIFICATION_POLICY_VERSION = 1
SUMMARY_KEYS = {"daily_reports", "weekly_reports", "monthly_reports"}
CURRENT_ADMIN_NOTIFICATION_OPTIONS = tuple(
    (
        key,
        label,
        "Работа бота, проверок и Telegram"
        if key == "admin_system"
        else description,
    )
    for key, label, description in ADMIN_NOTIFICATION_OPTIONS
)


class TelegramPanelRuntimeV37(TelegramPanelRuntimeV36):
    """Clear wheel controls, opt-in draw alerts and concise live status."""

    def __init__(self) -> None:
        super().__init__()
        self._welcome_on_start = False
        self._last_panel_heartbeat = 0.0
        self._panel_heartbeat_busy = False

    def record_runtime_heartbeat(self, *, force: bool = False) -> None:
        current_monotonic = time.monotonic()
        if (
            not force
            and self._last_panel_heartbeat
            and current_monotonic - self._last_panel_heartbeat < 300
        ):
            return
        if self._panel_heartbeat_busy:
            return
        self._panel_heartbeat_busy = True
        try:
            for attempt in range(1, 4):
                status = self.get_json_file("admin_panel_status.json", {})
                now_text = datetime.now(UTC).isoformat()
                status.update(
                    {
                        "status": "running",
                        "brand": "BB V.G.",
                        "last_heartbeat_at": now_text,
                        "version": 37,
                        "heartbeat_version": 1,
                        "runtime_owner": "telegram_control_center",
                        "update_consumer": "single_getUpdates_owner",
                    }
                )
                status.setdefault("started_at", now_text)
                try:
                    self.update_file(
                        "admin_panel_status.json",
                        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True)
                        + "\n",
                        "Update BB V.G. control center heartbeat [skip ci]",
                    )
                except RuntimeError as exc:
                    if attempt < 3 and any(code in str(exc) for code in (" 409 ", " 422 ")):
                        continue
                    raise
                self._last_panel_heartbeat = current_monotonic
                return
        except Exception as exc:
            print(f"WARNING panel heartbeat: {type(exc).__name__}: {exc}")
        finally:
            self._panel_heartbeat_busy = False

    # ---------- Notification policy ----------
    def notification_preferences(self, user_id: str | None = None) -> dict[str, bool]:
        prefs = super().notification_preferences(user_id)
        target = str(user_id or self.current_user_id or "")
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(target) if isinstance(users.get(target), dict) else {}
        raw = record.get("notification_preferences") if isinstance(record, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        if "wheel_draw_alerts" not in raw:
            prefs["wheel_draw_alerts"] = False
        for key in SUMMARY_KEYS:
            prefs[key] = False
        return prefs

    @staticmethod
    def _notification_options_for_role(
        role: str,
    ) -> tuple[tuple[str, str, str], ...]:
        options = tuple(WHEEL_NOTIFICATION_OPTIONS)
        if role in {"owner", "admin"}:
            options += CURRENT_ADMIN_NOTIFICATION_OPTIONS
        return options

    def _apply_notification_policy_once(self) -> bool:
        access = self.load_access(force=True)
        settings = access.setdefault("settings", {})
        try:
            installed = int(settings.get("notification_policy_version", 0) or 0)
        except (TypeError, ValueError):
            installed = 0
        if installed >= NOTIFICATION_POLICY_VERSION:
            return False

        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        for record in users.values():
            if not isinstance(record, dict):
                continue
            raw = record.get("notification_preferences")
            prefs = dict(raw) if isinstance(raw, dict) else {}
            prefs["wheel_draw_alerts"] = False
            for key in SUMMARY_KEYS:
                prefs.pop(key, None)
            record["notification_preferences"] = prefs

        settings["daily_reports"] = False
        settings["weekly_reports"] = False
        settings["notification_policy_version"] = NOTIFICATION_POLICY_VERSION
        self.save_access("Apply opt-in wheel time notification policy [skip ci]")
        return True

    def setup_bot(self) -> None:
        super().setup_bot()
        commands = [dict(item) for item in legacy.COMMANDS]
        for item in commands:
            if item.get("command") == "ranking":
                item["description"] = "Рейтинг источников"
            elif item.get("command") == "reports":
                item["description"] = "Аналитика по периодам"
        self.telegram_api("setMyCommands", {"commands": commands})
        try:
            changed = self._apply_notification_policy_once()
        except Exception as exc:
            print(f"WARNING notification policy migration: {type(exc).__name__}: {exc}")
            return
        if changed:
            try:
                # The monitor reads the encrypted preference bundle from its
                # checkout, so one replacement is required after this migration.
                self.dispatch("monitor.yml", {"continuous": "true", "replace": "true"})
            except Exception as exc:
                print(f"WARNING preference refresh dispatch: {type(exc).__name__}: {exc}")

    def register_user(self, message: dict[str, Any]) -> str:
        sender = message.get("from") if isinstance(message, dict) else None
        user_id = str(sender.get("id") or "") if isinstance(sender, dict) else ""
        access_before = self.load_access()
        users_before = (
            access_before.get("users")
            if isinstance(access_before.get("users"), dict)
            else {}
        )
        is_new = bool(user_id and user_id not in users_before)
        role = super().register_user(message)
        if not is_new or not user_id:
            return role

        access = self.load_access()
        record = access.get("users", {}).get(user_id)
        if not isinstance(record, dict):
            return role
        raw = record.get("notification_preferences")
        prefs = dict(raw) if isinstance(raw, dict) else {}
        prefs["wheel_draw_alerts"] = False
        for key in SUMMARY_KEYS:
            prefs.pop(key, None)
        record["notification_preferences"] = prefs
        self.save_access(
            f"Disable default wheel time alerts for new Telegram user {user_id} [skip ci]"
        )
        return role

    def show_notifications(self) -> None:
        prefs = self.notification_preferences()
        lines = [
            "🔔 <b>Уведомления</b>",
            "",
            "Каждый вид настраивается лично для вашего аккаунта.",
            "Уведомление о времени прокрутки изначально выключено.",
            "",
            "<b>Колёса</b>",
        ]
        rows: list[list[dict[str, Any]]] = []
        for key, label, description in WHEEL_NOTIFICATION_OPTIONS:
            enabled = bool(prefs.get(key, False))
            lines.append(
                f"{self.bool_mark(enabled)} {html.escape(label)} — {html.escape(description)}"
            )
            rows.append(
                [{
                    "text": f"{self.bool_mark(enabled)} {label}",
                    "callback_data": f"notify:{key}",
                }]
            )
        if self.is_admin():
            lines.extend(["", "<b>Административные</b>"])
            for key, label, description in CURRENT_ADMIN_NOTIFICATION_OPTIONS:
                enabled = bool(prefs.get(key, False))
                lines.append(
                    f"{self.bool_mark(enabled)} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [{
                        "text": f"{self.bool_mark(enabled)} {label}",
                        "callback_data": f"notify:{key}",
                    }]
                )
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def toggle_notification(self, key: str) -> None:
        if key in SUMMARY_KEYS:
            raise PermissionError("Автоматические сводки отключены")
        super().toggle_notification(key)

    def _remove_summary_preferences(self, *user_ids: str) -> None:
        access = self.load_access()
        changed = False
        for user_id in user_ids:
            record = access.get("users", {}).get(str(user_id))
            if not isinstance(record, dict):
                continue
            raw = record.get("notification_preferences")
            prefs = dict(raw) if isinstance(raw, dict) else {}
            before = dict(prefs)
            for key in SUMMARY_KEYS:
                prefs.pop(key, None)
            if prefs != before:
                record["notification_preferences"] = prefs
                changed = True
        if changed:
            self.save_access("Remove obsolete summary notification preferences [skip ci]")

    def set_admin(self, user_id: str, enabled: bool) -> None:
        super().set_admin(user_id, enabled)
        self._remove_summary_preferences(user_id)

    def transfer_owner(self, user_id: str) -> None:
        previous_owner = str(self.load_access().get("owner_id") or "")
        super().transfer_owner(user_id)
        self._remove_summary_preferences(user_id, previous_owner)

    # ---------- Main menu ----------
    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        admin = role in {"owner", "admin"}
        if self._welcome_on_start:
            text = (
                "🎡 <b>Добро пожаловать в BB V.G.</b>\n\n"
                "Бот проверяет Telegram-источники с колёсами BetBoom, "
                "сообщает о новых находках, показывает время прокрутки и хранит "
                "ваши личные отметки участия.\n\n"
                "Откройте активные колёса или выберите другой раздел."
            )
        else:
            text = "🎡 <b>BB V.G.</b>\n\nВыберите раздел."
        self.send(
            text,
            reply_markup={"inline_keyboard": self.compact_menu_rows(admin)},
        )

    def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold() if text else ""
        previous = self._welcome_on_start
        self._welcome_on_start = command == "/start" and len(text.split(maxsplit=1)) == 1
        try:
            super().handle_message(message)
        finally:
            self._welcome_on_start = previous

    # ---------- Wheel lifecycle ----------
    def dispatch_admin_action(self, action: str, value: str) -> dict[str, Any]:
        # The panel records intent only. The monitor is the sole writer of public
        # runtime state, so a concurrent rebase can no longer erase a button tap.
        command_id = admin_action_queue.enqueue_remote(action, value)
        return {
            "action": action,
            "queued": True,
            "command_id": command_id,
            "state_changed": False,
            "health_changed": False,
            "stats_changed": False,
            "detail": "Действие будет применено основной проверкой в ближайшем цикле.",
        }

    def _monitor_status(self) -> dict[str, Any]:
        try:
            return self.get_json_file("monitor_status.json", {})
        except Exception:
            return {}

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot(force=True)
        participating = self._joined_wheel_keys(snap)
        monitor_status = self._monitor_status()
        checked_at = monitor_status.get("last_successful_iteration_at")
        state_line = (
            f"🟢 обновлено {self.fmt_dt(checked_at)} ({self.age_text(checked_at)})"
            if checked_at
            else "⚪ ожидаются данные проверки"
        )
        if not items:
            self.send(
                "🔥 <b>Активных колёс сейчас нет.</b>\n\n"
                f"Состояние: {state_line}",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить", "callback_data": "refresh:active"}]]
                ),
            )
            return

        admin = self.is_admin()
        lines = [
            f"🔥 <b>Активные колёса: {len(items)}</b>",
            f"Состояние: {state_line}",
            "",
        ]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier).casefold()
            deadline = self.parse_dt(item.get("deadline"))
            joined = identifier.casefold() in participating or key in participating
            sources = self._sources_for_item(snap, key, item)
            source_text = ", ".join(f"@{source}" for source in sources) or "источник неизвестен"
            timing = self.remaining(deadline) if deadline else "🔴 Время прокрутки неизвестно"
            joined_text = "✅ участвуете" if joined else "❌ участие не отмечено"
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b> — {html.escape(timing)}",
                    f"   📡 {html.escape(source_text)} · {joined_text}",
                ]
            )

            url = str(item.get("url") or "")
            if url:
                buttons.append([{"text": f"🎡 {index} · {identifier[:18]}", "url": url}])
            actions: list[dict[str, str]] = []
            if not joined:
                actions.append({"text": "✅ Участвую", "callback_data": f"wheel:part:{key}"})
            if admin:
                actions.extend(
                    [
                        {"text": "🏁 Завершено", "callback_data": f"wheel:finished:{key}"},
                        {"text": "🚫 Неактивное", "callback_data": f"wheel:inactive:{key}"},
                    ]
                )
            else:
                actions.append({"text": "🙈 Скрыть", "callback_data": f"wheel:inactive:{key}"})
            if actions:
                buttons.append(actions)
            if admin:
                label = "⏱ Изменить время" if deadline else "⏱ Указать время"
                buttons.append([{"text": f"{label} для {index}", "callback_data": f"wheel:time:{key}"}])

        buttons.append([{"text": "🔄 Обновить", "callback_data": "refresh:active"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    @staticmethod
    def parse_manual_deadline(text: str) -> datetime | None:
        raw = str(text or "").strip().casefold().replace("ё", "е")
        # Compact H:MM values up to twelve hours are durations. Clock values
        # such as 18:30 retain their previous meaning.
        duration = re.fullmatch(r"(0|[1-9]|1[0-2]):([0-5]\d)", raw)
        if duration:
            return (
                datetime.now(legacy.DISPLAY_TZ)
                + timedelta(
                    hours=int(duration.group(1)),
                    minutes=int(duration.group(2)),
                )
            ).astimezone(UTC)
        return TelegramPanelRuntimeV36.parse_manual_deadline(raw)

    def request_manual_time(self, key: str) -> None:
        if not self.is_admin():
            raise PermissionError("Только администратор может задавать время")
        normalized = str(key).casefold()
        self.pending_input[int(self.current_user_id or 0)] = {
            "kind": "wheel_time",
            "key": normalized,
        }
        quick = [
            [
                {"text": "+15 мин", "callback_data": f"wheel:timequick:{normalized}:15"},
                {"text": "+30 мин", "callback_data": f"wheel:timequick:{normalized}:30"},
                {"text": "+45 мин", "callback_data": f"wheel:timequick:{normalized}:45"},
            ],
            [
                {"text": "+1 час", "callback_data": f"wheel:timequick:{normalized}:60"},
                {"text": "+1:15", "callback_data": f"wheel:timequick:{normalized}:75"},
                {"text": "+1:30", "callback_data": f"wheel:timequick:{normalized}:90"},
            ],
            [
                {"text": "+2 часа", "callback_data": f"wheel:timequick:{normalized}:120"},
                {"text": "+2:30", "callback_data": f"wheel:timequick:{normalized}:150"},
                {"text": "+3 часа", "callback_data": f"wheel:timequick:{normalized}:180"},
            ],
            [
                {"text": "+4 часа", "callback_data": f"wheel:timequick:{normalized}:240"},
                {"text": "+6 часов", "callback_data": f"wheel:timequick:{normalized}:360"},
                {"text": "+12 часов", "callback_data": f"wheel:timequick:{normalized}:720"},
            ],
        ]
        self.send(
            f"⏱ <b>Укажите время для <code>{html.escape(normalized)}</code></b>\n\n"
            "Нажмите быстрый вариант или отправьте сообщение:\n"
            "• <code>1:15</code> — через 1 час 15 минут;\n"
            "• <code>18:30</code> — ближайшие 18:30 по Барнаулу;\n"
            "• <code>14.07 18:30</code>;\n"
            "• <code>через 45 минут</code>;\n"
            "• <code>2 часа 15 минут</code>.\n\n"
            "Для отмены отправьте <code>/cancel</code>.",
            reply_markup=self.with_nav(quick),
        )

    def _set_quick_time(self, key: str, minutes: int) -> dict[str, Any]:
        if minutes not in {15, 30, 45, 60, 75, 90, 120, 150, 180, 240, 360, 720}:
            raise ValueError("Неизвестный шаблон времени")
        deadline = datetime.now(UTC) + timedelta(minutes=minutes)
        result = self.dispatch_admin_action(
            "set_deadline", f"{key.casefold()}|{deadline.isoformat()}"
        )
        self.pending_input.pop(int(self.current_user_id or 0), None)
        return {**result, "deadline": deadline}

    # ---------- Clear analytics and live status ----------
    def show_analytics(self, days: int = 1) -> None:
        days = days if days in {1, 7, 30} else 1
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        lines = [
            f"📊 <b>Аналитика {self.period_title(days)}</b>",
            "",
            "Публикация — это один пост канала со ссылкой на колесо. "
            "Источники — количество разных каналов, разместивших такие посты.",
            "Одинаковое колесо в нескольких каналах учитывается в публикациях, "
            "но повторное уведомление пользователю не отправляется.",
            "",
        ]
        if overview["wheel_posts"]:
            lines.append(f"🎡 Публикаций с колёсами: <b>{overview['wheel_posts']}</b>")
            lines.append(
                f"📡 Источников с публикациями: <b>{overview['sources_with_wheels']}</b>"
            )
            if days > 1:
                lines.append(
                    f"📈 В среднем публикаций за день: <b>{overview['wheel_posts'] / days:.1f}</b>"
                )
            if overview["top_sources"]:
                lines.extend(["", "<b>Самые активные источники</b>"])
                for index, (source, count) in enumerate(overview["top_sources"][:5], 1):
                    lines.append(f"{index}. @{html.escape(source)} — {count}")
        else:
            lines.append("За выбранный период публикаций с новыми колёсами не найдено.")

        lines.extend(["", "<b>Сейчас</b>"])
        lines.append(f"🔥 Активных колёс: <b>{overview['active']}</b>")
        if overview["active"]:
            lines.append(
                f"⏱ С указанным временем: <b>{overview['active_with_time']} из {overview['active']}</b>"
            )
            lines.append(
                f"✅ С вашей отметкой: <b>{overview['participating']} из {overview['active']}</b>"
            )

        rows: list[list[dict[str, str]]] = [[
            {"text": "Сегодня", "callback_data": "page:analytics:1"},
            {"text": "7 дней", "callback_data": "page:analytics:7"},
            {"text": "30 дней", "callback_data": "page:analytics:30"},
        ]]
        if self.is_admin():
            rows.append(
                [{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}]
            )
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_stats(self, days: int = 1) -> None:
        self.show_analytics(days)

    def show_reports(self) -> None:
        self.show_analytics(1)

    def show_period_report(self, days: int) -> None:
        self.show_analytics(days)

    def show_status(self) -> None:
        snap = self.snapshot(force=True)
        status = self._monitor_status()
        registry = self.load_source_registry()
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        configured = int(summary.get("total", 0) or 0) or len(snap.fast) + len(snap.nightly)
        checked = int(status.get("checked_sources", 0) or 0)
        reachable = int(status.get("reachable_sources", 0) or 0)
        errors = int(status.get("source_errors", 0) or 0)
        last = status.get("last_successful_iteration_at")
        fresh = self.parse_dt(last)
        working = bool(fresh and datetime.now(UTC) - fresh < timedelta(minutes=20))
        lines = [
            "✅ <b>Проверка работы системы</b>",
            "",
            f"Состояние: {'🟢 каналы проверяются по расписанию' if working else '🟡 данные проверки задерживаются'}",
            f"Последняя проверка каналов: <b>{self.fmt_dt(last)}</b> ({self.age_text(last)})",
            "",
            f"Настроено каналов: <b>{configured}</b>",
            f"Проверено в последнем цикле: <b>{checked}</b>",
            f"Доступно: <b>{reachable}</b>",
        ]
        if errors:
            lines.append(f"Требуют внимания: <b>{errors}</b>")
        lines.append(f"Активных колёс: <b>{len(self._collect_current_wheels())}</b>")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [
                    [{"text": "🔄 Обновить", "callback_data": "refresh:status"}],
                    [{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}],
                ]
            ),
        )

    def show_sources(self) -> None:
        registry = self.load_source_registry()
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        if not int(summary.get("total", 0) or 0):
            registry = self.source_registry_fallback()
            summary = registry["summary"]
        sources = registry.get("sources") if isinstance(registry.get("sources"), list) else []
        problems = [
            row
            for row in sources
            if isinstance(row, dict) and str(row.get("status") or "") != "available"
        ]
        unavailable = int(summary.get("unavailable", 0) or 0)
        pending = int(summary.get("pending", 0) or 0)
        state_text = "🟢 реестр актуален" if not unavailable and not pending else "🟡 есть каналы, требующие внимания"
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"Состояние: {state_text}",
            f"Реестр обновлён: {self.fmt_dt(registry.get('generated_at'))}",
            "",
            f"Всего в базе проверок: <b>{int(summary.get('total', 0) or 0)}</b>",
            f"Проверено: <b>{int(summary.get('checked', 0) or 0)}</b>",
            f"Доступно: <b>{int(summary.get('available', 0) or 0)}</b>",
        ]
        if unavailable:
            lines.append(f"Недоступно: <b>{unavailable}</b>")
        if pending:
            lines.append(f"Ожидают первой проверки: <b>{pending}</b>")
        if problems:
            lines.extend(["", "<b>Требуют внимания</b>"])
            for row in problems[:10]:
                username = str(row.get("username") or "неизвестно")
                reason = str(row.get("reason") or "нет данных")[:160]
                lines.append(f"• @{html.escape(username)} — {html.escape(reason)}")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(self.source_menu_rows(self.is_admin())),
        )

    def show_intelligence(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        state = self.intelligence_state()
        summary = state.get("last_run_summary") if isinstance(state.get("last_run_summary"), dict) else {}
        rows = self.intelligence_rows()
        new_rows = [row for row in rows if row.get("decision") == "new"]
        wheel_rows = [
            row for row in new_rows if int(row.get("wheel_links_found", 0) or 0) > 0
        ]
        try:
            run = self.workflow_run("source-intelligence.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status == "in_progress":
            state_text = "🔵 разведка выполняется"
        elif status in {"queued", "waiting", "pending"}:
            state_text = "🟡 ожидает запуска"
        elif status == "completed" and conclusion == "success":
            state_text = "🟢 последний запуск завершён"
        elif conclusion:
            state_text = "🔴 последний запуск завершился с ошибкой"
        else:
            state_text = "⚪ ещё не запускалась"
        text = (
            "🛰️ <b>Разведка новых источников</b>\n\n"
            f"Состояние: {state_text}\n"
            f"Последний запуск: {self.fmt_dt(state.get('last_run_at'))}\n\n"
            f"Просканировано каналов: <b>{int(summary.get('sources_scanned', 0) or 0)}</b>\n"
            f"Новых кандидатов: <b>{len(new_rows)}</b>\n"
            f"С найденными колёсами: <b>{len(wheel_rows)}</b>\n\n"
            "Разведка ищет ссылки и упоминания telegram.me внутри известных источников."
        )
        buttons = [
            [{"text": f"🆕 Новые находки ({len(new_rows)})", "callback_data": "intel:list:new:0"}],
            [{"text": f"🎡 С колёсами ({len(wheel_rows)})", "callback_data": "intel:list:wheels:0"}],
            [{"text": "▶️ Запустить разведку", "callback_data": "control:intelligence"}],
            [{"text": "🔄 Обновить состояние", "callback_data": "page:intelligence"}],
        ]
        self.send(text, reply_markup=self.with_nav(buttons))

    def show_discovery(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot(force=True)
        rows = self.candidate_rows()
        new_rows = [row for row in rows if row.get("category") == "new"]
        nightly_with_wheels = [row for row in rows if row.get("category") == "nightly"]
        try:
            run = self.workflow_run("nightly-discovery.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status == "in_progress":
            state_text = "🔵 ночная проверка выполняется"
        elif status in {"queued", "waiting", "pending"}:
            state_text = "🟡 ожидает запуска"
        elif status == "completed" and conclusion == "success":
            state_text = "🟢 последняя проверка завершена"
        elif conclusion:
            state_text = "🔴 последняя проверка завершилась с ошибкой"
        else:
            state_text = "⚪ данных о запуске нет"
        discovery_keys = {str(value).casefold() for value in snap.discovery.get("sources", {})}
        checked = sum(1 for name in snap.nightly if name.casefold() in discovery_keys)
        text = (
            "🌙 <b>Ночное наблюдение</b>\n\n"
            f"Состояние: {state_text}\n"
            f"Последнее завершение: {self.fmt_dt(snap.discovery.get('last_run_at'))}\n"
            f"Проверено: <b>{checked} из {len(snap.nightly)}</b>\n\n"
            f"Источников в ночном режиме: <b>{len(snap.nightly)}</b>\n"
            f"С найденными колёсами: <b>{len(nightly_with_wheels)}</b>\n"
            f"Новых кандидатов: <b>{len(new_rows)}</b>"
        )
        buttons = [
            [{"text": f"🆕 Требуют решения ({len(new_rows)})", "callback_data": "candidate:list:new:0"}],
            [{"text": f"🎡 С колёсами ({len(nightly_with_wheels)})", "callback_data": "candidate:list:nightly:0"}],
            [{"text": "▶️ Запустить ночную проверку", "callback_data": "control:nightly"}],
            [{"text": "🔄 Обновить состояние", "callback_data": "page:discovery"}],
        ]
        self.send(text, reply_markup=self.with_nav(buttons))

    def render_page(self, page: str) -> None:
        if page == "reports":
            self.show_analytics(1)
            return
        if page.startswith("report:") and page.split(":", 1)[1].isdigit():
            self.show_analytics(int(page.split(":", 1)[1]))
            return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data.startswith("wheel:timequick:"):
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            if not self.is_admin():
                self.answer(query_id, "Недоступно")
                return
            try:
                _, _, key, minutes_text = data.split(":", 3)
                result = self._set_quick_time(key, int(minutes_text))
            except Exception as exc:
                print(f"ERROR quick wheel time: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось сохранить время")
                return
            deadline = result["deadline"].astimezone(legacy.DISPLAY_TZ)
            self.answer(query_id, "Время принято")
            self.send(
                f"⏳ Время прокрутки принято: <b>{deadline:%d.%m.%Y %H:%M}</b> "
                "по Барнаулу. Оно появится после ближайшей проверки.",
                reply_markup=self.with_nav(
                    [[{"text": "🔥 К активным колёсам", "callback_data": "page:active"}]]
                ),
            )
            return
        if data == "summary:send" or data.startswith("summary:send:") or data == "control:daily":
            self._prepare_callback_user(query)
            self.answer(str(query.get("id") or ""), "Автоматическая отправка отключена")
            self.show_analytics(1)
            return
        super().handle_callback(query)


def _callbacks(rows: list[list[dict[str, Any]]]) -> list[str]:
    return [str(button.get("callback_data") or "") for row in rows for button in row]


def self_test() -> None:
    panel = TelegramPanelRuntimeV37()
    reference = datetime.now(UTC)
    duration = panel.parse_manual_deadline("1:15")
    assert duration is not None
    assert 74 <= (duration - reference).total_seconds() / 60 <= 76
    clock = panel.parse_manual_deadline("18:30")
    assert clock is not None

    assert "summary:send" not in TelegramPanelRuntimeV37.show_analytics.__code__.co_consts
    assert "🌙 Открыть ночное наблюдение" not in TelegramPanelRuntimeV37.show_intelligence.__code__.co_consts
    assert {key for key, _, _ in panel._notification_options_for_role("user")} == {
        "wheels",
        "wheel_final_reminders",
        "wheel_draw_alerts",
    }
    assert not SUMMARY_KEYS & {
        key for key, _, _ in panel._notification_options_for_role("admin")
    }

    panel.load_access = lambda force=False: {  # type: ignore[method-assign]
        "owner_id": "1",
        "admins": [],
        "users": {"1": {"notification_preferences": {"wheels": True}}},
        "settings": {},
    }
    panel.current_user_id = "1"
    panel.current_role = "owner"
    prefs = panel.notification_preferences("1")
    assert prefs["wheel_draw_alerts"] is False
    assert prefs["daily_reports"] is False
    assert prefs["weekly_reports"] is False

    migration_panel = TelegramPanelRuntimeV37()
    migration_access = {
        "owner_id": "1",
        "admins": [],
        "users": {
            "1": {
                "notification_preferences": {
                    "wheels": True,
                    "wheel_draw_alerts": True,
                    "daily_reports": True,
                    "weekly_reports": True,
                }
            },
            "2": {
                "notification_preferences": {
                    "wheels": True,
                    "wheel_draw_alerts": True,
                }
            },
        },
        "settings": {"daily_reports": True, "weekly_reports": True},
    }
    migration_saves: list[str] = []
    migration_panel.load_access = lambda force=False: migration_access  # type: ignore[method-assign]
    migration_panel.save_access = lambda message: migration_saves.append(message)  # type: ignore[method-assign]
    assert migration_panel._apply_notification_policy_once() is True
    assert migration_saves
    for record in migration_access["users"].values():
        record_prefs = record["notification_preferences"]
        assert record_prefs["wheel_draw_alerts"] is False
        assert not SUMMARY_KEYS & set(record_prefs)
    assert migration_panel._apply_notification_policy_once() is False

    summary_workflow = Path(".github/workflows/daily-report.yml").read_text(encoding="utf-8")
    assert "schedule:" not in summary_workflow

    captured: list[tuple[str, dict[str, Any]]] = []
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        state={"active_wheels": {}},
        stats={"daily": {}, "sources": {}},
        health={"sources": {}},
        discovery={"sources": {}},
        fast=[],
        nightly=[],
    )
    panel._monitor_status = lambda: {  # type: ignore[method-assign]
        "last_successful_iteration_at": datetime.now(UTC).isoformat()
    }
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
    panel.show_active()
    assert captured and "Состояние:" in captured[-1][0]
    assert "refresh:active" in str(captured[-1][1])

    captured.clear()
    panel._collect_current_wheels = lambda: [{  # type: ignore[method-assign]
        "_key": "wheel-one",
        "identifier": "wheel-one",
        "url": "https://betboom.ru/freestream/wheel-one",
        "source": "source_one",
    }]
    panel._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
    panel.is_admin = lambda: True  # type: ignore[method-assign]
    panel.show_active()
    active_markup = str(captured[-1][1].get("reply_markup"))
    assert "wheel:part:wheel-one" in active_markup
    assert "wheel:finished:wheel-one" in active_markup
    assert "wheel:inactive:wheel-one" in active_markup
    assert "wheel:time:wheel-one" in active_markup
    print("admin panel v37 interface and notification policy self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV37().run()


if __name__ == "__main__":
    raise SystemExit(main())
