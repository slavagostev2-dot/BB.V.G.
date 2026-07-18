from __future__ import annotations

import argparse
import copy
import html
import json
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import admin_action_queue
import admin_bot as legacy
import bot_notification_state  # noqa: F401  # installs notification delivery policies
import personal_wheel_voting
import telegram_ui
from bbvg.bot.storage import INTERVAL_OPTIONS, PrivateStateRuntime
from bbvg.bot.users import UserSettingsMixin

SUMMARY_PERIODS = {
    "daily": (1, "Ежедневная"),
    "weekly": (7, "Еженедельная"),
    "monthly": (30, "Ежемесячная"),
}


def _install_dedicated_vote_key_contract() -> None:
    """Prevent BOT_TOKEN from becoming a pseudonym key in the live panel."""

    if getattr(personal_wheel_voting, "_bbvg_dedicated_vote_key_installed", False):
        return
    original = personal_wheel_voting.actor_vote_token

    def dedicated_actor_vote_token(user_id: str, secret: str | None = None) -> str:
        dedicated = str(secret or os.getenv("BOT_STATE_KEY") or "").strip()
        if not dedicated:
            raise RuntimeError("BOT_STATE_KEY is required for personal vote pseudonyms")
        return original(user_id, secret=dedicated)

    personal_wheel_voting.actor_vote_token = dedicated_actor_vote_token
    personal_wheel_voting._bbvg_dedicated_vote_key_installed = True


_install_dedicated_vote_key_contract()
PersonalWheelVotingMixin = personal_wheel_voting.PersonalWheelVotingMixin

RAINBOW_DOTS = ("🔵", "🟢", "🟡", "🟣", "🟠", "🔴")
_DOT_GROUP = "(?:" + "|".join(re.escape(value) for value in RAINBOW_DOTS) + ")"
_WHEEL_LINE_COLOR_RE = re.compile(
    rf"(?m)^(<b>\d+\. <code>.*?</code>)\s+{_DOT_GROUP}(</b>)$"
)
_BUTTON_COLOR_RE = re.compile(
    rf"^{_DOT_GROUP}\s+(?=(?:🎡|✅|🏁|🚫|⏱|🔄|🏠|\d))"
)
_BUTTON_INDEX_RE = re.compile(r"(?<!\d)(\d+)\s*·")
TECHNICAL_ERROR_RE = re.compile(
    r"(?:Не удалось выполнить команду:\s*<code>[^<]+</code>|"
    r"Диагностика не выполнена:\s*(?:<code>)?[^<.\n]+(?:</code>)?|"
    r"(?:ошибка|не удалось)[^\n]{0,180}<code>[^<]{1,120}</code>|"
    r"Traceback \(most recent call last\))",
    re.IGNORECASE | re.DOTALL,
)
USER_ACTION_ERROR = (
    "⚠️ <b>Не удалось выполнить действие.</b>\n\n"
    "Попробуйте ещё раз или вернитесь в главное меню."
)


class TelegramPanelRuntime(
    PersonalWheelVotingMixin,
    UserSettingsMixin,
    PrivateStateRuntime,
):
    """Current Telegram control center without version-layer inheritance."""

    RUNTIME_VERSION = 41

    def __init__(self) -> None:
        super().__init__()
        self._last_panel_heartbeat = 0.0
        self._panel_heartbeat_busy = False

    @staticmethod
    def safe_text_for_role(text: str, role: str) -> str:
        value = str(text or "")
        if role not in {"owner", "admin"} and TECHNICAL_ERROR_RE.search(value):
            return USER_ACTION_ERROR
        return value

    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        value = self.safe_text_for_role(text, self.current_role)
        return super().send(value, reply_markup=reply_markup, chat_id=chat_id)

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
                self.dispatch("monitor.yml", {"continuous": "true", "replace": "true"})
            except Exception as exc:
                print(f"WARNING preference refresh dispatch: {type(exc).__name__}: {exc}")

    def dispatch_admin_action(self, action: str, value: str) -> dict[str, Any]:
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
                now_text = datetime.now(legacy.UTC).isoformat()
                status.update(
                    {
                        "status": "running",
                        "brand": "BB V.G.",
                        "last_heartbeat_at": now_text,
                        "version": self.RUNTIME_VERSION,
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

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")

        if data == "bb:l:active":
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                self._prepare_callback_user(query)
                self.answer(query_id, "Открываю активные колёса")
                self.open_page("active")
            finally:
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith("interval:"):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                self._prepare_callback_user(query)
                if not self.is_admin():
                    self.answer(query_id, "Недостаточно прав")
                    return
                try:
                    minutes = int(data.split(":", 1)[1])
                    self.set_interval(minutes)
                except (PermissionError, ValueError):
                    self.answer(query_id, "Недоступное значение")
                    return
                self.answer(query_id, f"Интервал: {minutes} мин.")
                self.show_interval()
            finally:
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith(("bb:p:", "wheel:part:")):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            original_show_active = self.show_active
            self._edit_message_id = int(message.get("message_id") or 0) or None
            self.show_active = (  # type: ignore[method-assign]
                lambda page=0: self.show_menu(clear_stack=True)
            )
            try:
                super().handle_callback(query)
            finally:
                self.show_active = original_show_active  # type: ignore[method-assign]
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith(("bb:t:", "wheel:time:", "wheel:timequick:")):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                self._prepare_callback_user(query)
                self.answer(query_id, "Ручное указание времени отключено")
                self.show_menu(clear_stack=True)
            finally:
                self._edit_message_id = previous_edit_message_id
            return

        if data == "summary:send" or data.startswith("summary:send:") or data == "control:daily":
            self._prepare_callback_user(query)
            if not self.is_admin():
                self.answer(query_id, "Недоступно")
                return
            if data == "summary:send":
                self.answer(query_id, "Выберите период")
                self.show_send_summary_menu()
                return
            period = "daily" if data == "control:daily" else data.rsplit(":", 1)[1]
            if period not in SUMMARY_PERIODS:
                self.answer(query_id, "Неизвестный период")
                return
            days, label = SUMMARY_PERIODS[period]
            self.answer(query_id, "Сводка сформирована")
            self.send(
                f"📨 <b>{html.escape(label)} сводка</b>\n\n"
                "Сводка сформирована непосредственно ботом без отдельного технического запуска.",
                reply_markup=self.with_nav(),
            )
            self.show_period_report(days)
            return
        super().handle_callback(query)

    @classmethod
    def _simplify_active_payload(
        cls,
        text: str,
        reply_markup: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        cleaned_text = _WHEEL_LINE_COLOR_RE.sub(r"\1\2", str(text or ""))
        if not isinstance(reply_markup, dict):
            return cleaned_text, reply_markup
        cleaned_markup = copy.deepcopy(reply_markup)
        rows: list[list[dict[str, Any]]] = []
        for row in cleaned_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            filtered: list[dict[str, Any]] = []
            for button in row:
                if not isinstance(button, dict):
                    continue
                callback = str(button.get("callback_data") or "")
                if callback.startswith(("bb:t:", "wheel:time:")):
                    continue
                item = dict(button)
                label = str(item.get("text") or "")
                item["text"] = _BUTTON_COLOR_RE.sub("", label)
                filtered.append(item)
            if filtered:
                rows.append(filtered)
        cleaned_markup["inline_keyboard"] = rows
        return cleaned_text, cleaned_markup

    @classmethod
    def _color_active_payload(
        cls,
        text: str,
        reply_markup: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Compatibility alias retained while old tests and workflows are migrated."""
        return cls._simplify_active_payload(text, reply_markup)


    def _registry_snapshot(self, snap: Any) -> dict[str, Any]:
        registry = self.load_source_registry()
        if not registry.get("sources"):
            registry = self.source_registry_fallback()
        if not registry.get("generated_at"):
            candidates = [
                str(row.get("last_checked_at") or "")
                for row in registry.get("sources", [])
                if isinstance(row, dict) and str(row.get("last_checked_at") or "")
            ]
            registry["generated_at"] = max(candidates, default=None)
        return registry

    def show_sources(self) -> None:
        snap = self.snapshot(force=True)
        registry = self._registry_snapshot(snap)
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        groups = self.source_sets(snap)
        primary = groups.get("primary", [])
        reserve = groups.get("reserve", [])
        generated_at = registry.get("generated_at")
        updated = (
            f"{self.fmt_dt(generated_at)} ({self.age_text(generated_at)})"
            if generated_at
            else "время обновления пока не записано"
        )
        problems = int(summary.get("unavailable", 0) or 0) + int(
            summary.get("pending", 0) or 0
        )
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"Всего в реестре: <b>{int(summary.get('total', len(primary) + len(reserve)) or 0)}</b>",
            f"Основная проверка: <b>{int(summary.get('primary', len(primary)) or 0)}</b>",
            f"Ночное наблюдение: <b>{int(summary.get('nightly', len(reserve)) or 0)}</b>",
            f"Проверено: <b>{int(summary.get('checked', 0) or 0)}</b>",
            f"Доступно: <b>{int(summary.get('available', 0) or 0)}</b>",
            f"Требуют внимания: <b>{problems}</b>",
            f"Реестр обновлён: <b>{html.escape(updated)}</b>",
        ]
        rows = self.source_menu_rows(self.is_admin())
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_analytics(self, days: int = 1) -> None:
        days = days if days in {1, 7, 30} else 1
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        totals = self.period_totals(snap.stats, days)
        current = self._collect_current_wheels()
        multi_source = sum(
            len(
                self._sources_for_item(
                    snap,
                    str(item.get("_key") or item.get("identifier") or ""),
                    item,
                )
            ) > 1
            for item in current
        )

        rated: list[tuple[str, int, int]] = []
        latest_source = ""
        latest_at: datetime | None = None
        source_rows = snap.stats.get("sources") if isinstance(snap.stats, dict) else {}
        if isinstance(source_rows, dict):
            for source, raw in source_rows.items():
                if not isinstance(raw, dict):
                    continue
                score = int(raw.get("quality_score", 0) or 0)
                votes = int(raw.get("personal_votes", 0) or 0)
                if score > 0:
                    rated.append((str(source), score, votes))
                candidate = self.parse_dt(raw.get("last_wheel_post_at"))
                if candidate and (latest_at is None or candidate > latest_at):
                    latest_at = candidate
                    latest_source = str(source)
        rated.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        registry = self._registry_snapshot(snap)
        registry_summary = (
            registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        )

        lines = [
            f"📊 <b>Аналитика {html.escape(self.period_title(days))}</b>",
            "",
            "<b>Находки</b>",
            f"🎡 Публикаций с колёсами: <b>{overview['wheel_posts']}</b>",
            f"📡 Источников с находками: <b>{overview['sources_with_wheels']}</b>",
            f"🔔 Отправлено уведомлений: <b>{overview['notifications']}</b>",
            f"🛡 Повторов подавлено: <b>{int(totals.get('duplicates_suppressed', 0) or 0)}</b>",
            f"⚠️ Ошибок источников: <b>{int(totals.get('errors', 0) or 0)}</b>",
        ]
        if days > 1:
            lines.append(
                f"📈 Среднее публикаций в день: <b>{overview['wheel_posts'] / days:.1f}</b>"
            )
            if overview.get("best_day"):
                best_day, best_count = overview["best_day"]
                lines.append(f"⭐ Лучший день: <b>{html.escape(str(best_day))}</b> — {best_count}")
        if overview.get("top_sources"):
            lines.extend(["", "<b>Топ источников по находкам</b>"])
            for index, (source, count) in enumerate(overview["top_sources"][:5], 1):
                lines.append(f"{index}. @{html.escape(source)} — {count}")

        lines.extend(
            [
                "",
                "<b>Участие и рейтинг</b>",
                f"🙋 Личных голосов: <b>{int(totals.get('personal_votes', 0) or 0)}</b>",
                f"🏆 Начислено очков источникам: <b>{int(totals.get('personal_vote_points', 0) or 0)}</b>",
                f"📊 Источников с рейтингом: <b>{len(rated)}</b>",
            ]
        )
        if rated:
            lines.append(
                f"🥇 Лидер: <b>@{html.escape(rated[0][0])}</b> — "
                f"{rated[0][1]} оч. ({rated[0][2]} голос.)"
            )

        lines.extend(
            [
                "",
                "<b>Сейчас</b>",
                f"🔥 Активных колёс: <b>{overview['active']}</b>",
                f"⏱ С известным временем: <b>{overview['active_with_time']}</b>",
                f"🔗 Найдены в нескольких каналах: <b>{multi_source}</b>",
                f"✅ Вы участвуете: <b>{overview['participating']}</b>",
            ]
        )
        if latest_at:
            lines.append(
                f"🕘 Последняя находка: <b>@{html.escape(latest_source)}</b>, "
                f"{self.fmt_dt(latest_at.isoformat())}"
            )
        lines.extend(
            [
                "",
                "<b>Покрытие источников</b>",
                f"✅ Доступно: <b>{int(registry_summary.get('available', 0) or 0)} из "
                f"{int(registry_summary.get('total', 0) or 0)}</b>",
            ]
        )
        if registry.get("generated_at"):
            lines.append(f"🗂 Реестр обновлён: <b>{self.fmt_dt(registry['generated_at'])}</b>")

        rows: list[list[dict[str, str]]] = [self._period_buttons(days)]
        if self.is_admin():
            rows.append([{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_ranking(self) -> None:
        snap = self.snapshot(force=True)
        rows: list[tuple[str, int, int]] = []
        source_rows = snap.stats.get("sources") if isinstance(snap.stats, dict) else {}
        if isinstance(source_rows, dict):
            for source, raw in source_rows.items():
                if not isinstance(raw, dict):
                    continue
                score = int(raw.get("quality_score", 0) or 0)
                if score > 0:
                    rows.append((str(source), score, int(raw.get("personal_votes", 0) or 0)))
        rows.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            "Пользователь даёт каждому источнику события 1 очко; "
            "администратор или владелец — 5 очков.",
            "Если колесо найдено в нескольких каналах, одинаковый вес получает каждый канал.",
            "",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score, votes) in enumerate(rows[:20], 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(
                f"{mark} <b>@{html.escape(source)}</b> — <b>{score}</b> оч. ({votes} голос.)"
            )
        if not rows:
            lines.append("Пока нет источников с положительным рейтингом.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить рейтинг", "callback_data": "page:ranking"}]]
            ),
        )

    def show_active(self, page: int = 0) -> None:
        original_send = self.send

        def simplified_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            cleaned_text, cleaned_markup = self._simplify_active_payload(
                text, reply_markup
            )
            return original_send(
                cleaned_text,
                reply_markup=cleaned_markup,
                chat_id=chat_id,
            )

        self.send = simplified_send  # type: ignore[method-assign]
        try:
            super().show_active(page)
        finally:
            self.send = original_send  # type: ignore[method-assign]

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        admin = role in {"owner", "admin"}
        rows = [
            [
                dict(button)
                for button in row
                if str(button.get("callback_data") or "") != "page:status"
            ]
            for row in self.compact_menu_rows(admin)
        ]
        rows = [row for row in rows if row]
        text = (
            "🎡 <b>BB V.G.</b>\n\n"
            "Находит колёса BetBoom, показывает время прокрутки и хранит отметки участия.\n\n"
            f"Ваша роль: <b>{html.escape(self.role_name(role))}</b>\n\n"
            "Выберите раздел."
        )
        self.send(text, reply_markup={"inline_keyboard": rows})

    def show_settings(self) -> None:
        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
            [{"text": "✅ Работа системы", "callback_data": "page:status"}],
        ]
        lines = [
            "⚙️ <b>Настройки</b>",
            "",
            "Личные настройки применяются только к вашему Telegram-аккаунту.",
        ]
        if self.is_admin():
            rows.extend(
                [
                    [{"text": "🧭 API и Legacy", "callback_data": "page:wheelmode"}],
                    [{"text": "⛔ Отключённый функционал", "callback_data": "page:disabled_features"}],
                ]
            )
            interval = int(
                self.load_access().get("settings", {}).get("monitor_interval_minutes", 5)
            )
            lines.extend(["", f"Интервал постоянной проверки: <b>{interval} мин.</b>"])
            rows.append([{"text": "⏱ Интервал проверки", "callback_data": "page:interval"}])
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
        else:
            rows.append([{"text": "🗑 Удалить мои данные", "callback_data": "privacy:delete:ask"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_interval(self) -> None:
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        current = int(
            self.load_access().get("settings", {}).get("monitor_interval_minutes", 5)
        )
        buttons: list[list[dict[str, str]]] = []
        for offset in range(0, len(INTERVAL_OPTIONS), 2):
            row: list[dict[str, str]] = []
            for minutes in INTERVAL_OPTIONS[offset : offset + 2]:
                row.append(
                    {
                        "text": f"{'✅ ' if minutes == current else ''}{minutes} мин.",
                        "callback_data": f"interval:{minutes}",
                    }
                )
            buttons.append(row)
        self.send(
            "⏱ <b>Интервал проверки</b>\n\n"
            "Выберите, как часто проверять все основные источники. "
            "Изменение сохраняется сразу и перезапускает непрерывный монитор.",
            reply_markup=self.with_nav(buttons),
        )

    def set_interval(self, minutes: int) -> None:
        if not self.is_admin() or minutes not in INTERVAL_OPTIONS:
            raise PermissionError("Недоступное значение")
        access = self.load_access()
        access.setdefault("settings", {})["monitor_interval_minutes"] = minutes
        self.save_access("Update monitor interval via Telegram [skip ci]")
        try:
            self.dispatch(
                "monitor.yml",
                {"continuous": "true", "replace": "true"},
            )
        except Exception as exc:
            # The saved setting is authoritative and will still be read by the
            # running monitor on its next cycle.
            print(f"WARNING monitor interval restart: {type(exc).__name__}: {exc}")

    def show_disabled_features(self) -> None:
        text = (
            "⛔ <b>Отключённый функционал</b>\n\n"
            "• <b>Ручное указание времени</b> — скрыто и отключено: время берётся из "
            "BetBoom API, а без серверного времени действует штатное двухчасовое окно.\n"
            "• <b>Общее «Участвую»</b> — отключено: отметка всегда принадлежит только "
            "нажавшему пользователю.\n"
            "• <b>«Завершено» и «Неактивное»</b> — отключены: они конфликтуют с "
            "авторитетной BetBoom API-проверкой и могли удалить колесо раньше сервера.\n"
            "• <b>Скрытие или удаление пользователем</b> — отключено: общий список должен "
            "быть одинаковым, а личное участие хранится отдельно.\n"
            "• <b>Legacy HTML-checker</b> — не работает параллельно с API, чтобы одна ссылка "
            "не получала противоречивые статусы."
        )
        self.send(text, reply_markup=self.with_nav())

    def render_page(self, page: str) -> None:
        normalized = self._normalize_page(page)
        if normalized in {"wheelmode", "disabled_features"} and not self.is_admin():
            self.show_settings()
            return
        if normalized == "app":
            self.send(
                "📦 <b>Приложение временно отключено</b>\n\n"
                "Рабочий контур BB V.G. сейчас находится только в Telegram-боте.",
                reply_markup=self.with_nav(),
            )
            return
        if normalized == "interval":
            self.show_interval()
            return
        if normalized.startswith("active:"):
            value = normalized.split(":", 1)[1]
            self.show_active(int(value) if value.isdigit() else 0)
            return
        if normalized.startswith("analytics:"):
            value = normalized.split(":", 1)[1]
            self.show_analytics(int(value) if value.isdigit() else 1)
            return
        if normalized.startswith("access:"):
            value = normalized.split(":", 1)[1]
            self.show_access(int(value) if value.isdigit() else 0)
            return
        if normalized.startswith("recipients:"):
            value = normalized.split(":", 1)[1]
            self.show_recipients(int(value) if value.isdigit() else 0)
            return
        if normalized == "report:inactive":
            self.show_inactive_report(0)
            return
        if normalized.startswith("report:inactive:"):
            value = normalized.rsplit(":", 1)[1]
            self.show_inactive_report(int(value) if value.isdigit() else 0)
            return
        if normalized == "status":
            self.show_status()
            return
        super().render_page(normalized)


def _configured_panel(
    panel: TelegramPanelRuntime,
    captured: list[tuple[str, dict[str, Any]]],
) -> None:
    panel.current_user_id = "1"
    panel.current_chat_id = "1"
    panel.current_role = "admin"
    panel.is_admin = lambda: True  # type: ignore[method-assign]
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        state={"active_wheels": {}},
        stats={"sources": {}, "daily": {}},
        health={"sources": {}},
        discovery={"sources": {}},
        fast=[],
        nightly=[],
    )
    panel._monitor_status = lambda: {}  # type: ignore[method-assign]
    panel._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
    panel._personal_participating_wheels = lambda: set()  # type: ignore[method-assign]
    panel._sources_for_item = lambda snap, key, item: ["source", "second"]  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
        {
            "_key": "wheel-a",
            "identifier": "wheel-a",
            "source": "source",
            "sources": ["source", "second"],
            "action_id": 101,
            "url": "https://betboom.ru/freestream/wheel-a",
        }
    ]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]


def self_test() -> None:
    assert TelegramPanelRuntime.RUNTIME_VERSION == 41
    assert issubclass(TelegramPanelRuntime, PersonalWheelVotingMixin)
    assert not any(
        cls.__module__.startswith("admin_panel_runtime_v")
        for cls in TelegramPanelRuntime.__mro__
    )
    assert SUMMARY_PERIODS["daily"][0] == 1
    assert SUMMARY_PERIODS["weekly"][0] == 7
    assert SUMMARY_PERIODS["monthly"][0] == 30

    captured: list[tuple[str, dict[str, Any]]] = []
    panel = TelegramPanelRuntime()
    _configured_panel(panel, captured)
    panel.show_active()
    active_text, active_kwargs = captured[-1]
    active_markup = str(active_kwargs["reply_markup"])
    assert "@source, @second" in active_text
    assert "wheel:part:wheel-a" in active_markup
    assert "wheel:time:wheel-a" not in active_markup
    assert "wheel:finished:" not in active_markup
    assert "wheel:inactive:" not in active_markup

    cleaned_text, cleaned_markup = panel._simplify_active_payload(
        "<b>1. <code>wheel-a</code> 🔵</b>\n🔴 Время прокрутки неизвестно",
        {
            "inline_keyboard": [
                [{"text": "🔵 🎡 1 · Открыть колесо", "url": "https://example.com"}],
                [{"text": "🔵 ✅ 1 · Участвую", "callback_data": "join:wheel-a"}],
                [{"text": "⏱ 1 · Указать время", "callback_data": "wheel:time:wheel-a"}],
            ]
        },
    )
    assert "<code>wheel-a</code> 🔵" not in cleaned_text
    assert cleaned_markup is not None
    assert "wheel:time:" not in str(cleaned_markup)
    assert not telegram_ui.markup_issues(cleaned_markup)

    panel.navigation = {"1": ["menu"]}
    panel.role_for = lambda user_id: "admin"  # type: ignore[method-assign]
    panel.role_name = lambda role: "Администратор"  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.show_menu()
    assert "Ваша роль: <b>Администратор</b>" in captured[-1][0]
    panel.show_disabled_features()
    assert "Ручное указание времени" in captured[-1][0]

    callback_calls: list[tuple[str, Any]] = []
    panel._prepare_callback_user = lambda query: callback_calls.append(("prepare", query))  # type: ignore[method-assign]
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {"token": {"wheel_key": "wheel-a"}}}
    )
    panel.mark_personal_participation = lambda key: {"changed": True}  # type: ignore[method-assign]
    panel.answer = lambda query_id, text: callback_calls.append(("answer", text))  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: callback_calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "q",
            "data": "bb:p:token",
            "message": {"message_id": 77, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("menu", True) in callback_calls
    assert panel._edit_message_id is None

    print("BB V.G. consolidated Telegram panel runtime self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntime().run()


if __name__ == "__main__":
    raise SystemExit(main())
