from __future__ import annotations

import html
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from admin_panel_v2 import TelegramPanelV2
from admin_bot import DISPLAY_TZ
from bbvg.bot.foundation import PanelFoundationMixin
import telegram_ui

INTELLIGENCE_PER_PAGE = 6
INACTIVE_PAGE_SIZE = 10

UTC = timezone.utc


class PanelInterfaceRuntime(PanelFoundationMixin, TelegramPanelV2):
    """Current compact panel interface formerly distributed across v14-v16."""

    def __init__(self) -> None:
        super().__init__()
        self._edit_message_id: int | None = None
        self._remove_reply_keyboard_before_send = False

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [
            [
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
                {"text": "📊 Аналитика", "callback_data": "page:analytics"},
            ],
            [
                {"text": "📡 Источники", "callback_data": "page:sources"},
                {"text": "⚙️ Настройки", "callback_data": "page:settings"},
            ],
        ]
        rows.append(
            [{
                "text": "🛠 Управление" if admin else "✅ Работа системы",
                "callback_data": "page:control" if admin else "page:status",
            }]
        )
        return rows

    @staticmethod
    def period_title(days: int) -> str:
        if days == 1:
            return "сегодня"
        if days == 7:
            return "за 7 дней"
        if days == 30:
            return "за 30 дней"
        return f"за {days} дней"

    @staticmethod
    def _period_buttons(days: int) -> list[dict[str, str]]:
        values = ((1, "Сегодня"), (7, "7 дней"), (30, "30 дней"))
        return [
            {
                "text": ("✓ " if value == days else "") + label,
                "callback_data": f"page:analytics:{value}",
            }
            for value, label in values
        ]

    @staticmethod
    def analytics_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        first = [{"text": "📊 Статистика", "callback_data": "page:stats:1"}]
        if admin:
            first.append({"text": "📅 Сводки", "callback_data": "page:reports"})
        return [
            first,
            [{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}],
        ]

    @staticmethod
    def control_menu_rows() -> list[list[dict[str, Any]]]:
        return [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "✅ Состояние системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]

    @staticmethod
    def summary_send_rows() -> list[list[dict[str, Any]]]:
        return [
            [{"text": "За день", "callback_data": "summary:send:daily"}],
            [{"text": "За неделю", "callback_data": "summary:send:weekly"}],
            [{"text": "За месяц", "callback_data": "summary:send:monthly"}],
        ]

    def show_send_summary_menu(self) -> None:
        if not self.is_admin():
            self.send(
                "Отправка сводок доступна только администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        self.send(
            "📨 <b>Отправить сводку</b>\n\nВыберите период:",
            reply_markup=self.with_nav(self.summary_send_rows()),
        )

    def _monitor_status(self) -> dict[str, Any]:
        try:
            return self.get_json_file("monitor_status.json", {})
        except Exception:
            return {}

    @staticmethod
    def _normalize_page(page: str) -> str:
        value = str(page or "menu")
        if value in {"analytics", "reports"}:
            return "analytics:1"
        if value.startswith("stats:"):
            period = value.split(":", 1)[1]
            return f"analytics:{period}" if period.isdigit() else "analytics:1"
        if value.startswith("report:"):
            period = value.split(":", 1)[1]
            if period.isdigit():
                return f"analytics:{period}"
        if value == "active":
            return "active:0"
        if value == "access":
            return "access:0"
        if value == "recipients":
            return "recipients:0"
        return value

    @classmethod
    def _page_family(cls, page: str) -> str:
        value = cls._normalize_page(page)
        for prefix in ("analytics:", "active:", "access:", "recipients:"):
            if value.startswith(prefix):
                return prefix.rstrip(":")
        for prefix in ("source_list:", "candidate_list:", "intel_list:"):
            if value.startswith(prefix):
                return ":".join(value.split(":")[:2])
        if value == "report:inactive" or value.startswith("report:inactive:"):
            return "report:inactive"
        return value

    def open_page(self, page: str, *, push: bool = True) -> None:
        normalized = self._normalize_page(page)
        stack = self.stack()
        if push:
            if stack and self._page_family(stack[-1]) == self._page_family(normalized):
                stack[-1] = normalized
            elif not stack or stack[-1] != normalized:
                stack.append(normalized)
        self.render_page(normalized)

    def period_overview(self, snap: Any, days: int) -> dict[str, Any]:
        totals = self.period_totals(snap.stats, days)
        today = datetime.now(DISPLAY_TZ).date()
        allowed = {today.isoformat()}
        if days > 1:
            allowed = {
                today.fromordinal(today.toordinal() - offset).isoformat()
                for offset in range(days)
            }
        source_counts: dict[str, int] = {}
        day_counts: dict[str, int] = {}
        for day, entry in snap.stats.get("daily", {}).items():
            if day not in allowed or not isinstance(entry, dict):
                continue
            day_counts[day] = int((entry.get("totals") or {}).get("wheel_posts", 0) or 0)
            for source, source_entry in (entry.get("sources") or {}).items():
                if not isinstance(source_entry, dict):
                    continue
                count = int(source_entry.get("wheel_posts", 0) or 0)
                if count > 0:
                    source_counts[str(source)] = source_counts.get(str(source), 0) + count
        active_rows = [
            (str(key), entry)
            for key, entry in snap.state.get("active_wheels", {}).items()
            if isinstance(entry, dict)
        ]
        active_keys = {
            str(entry.get("identifier") or key).casefold()
            for key, entry in active_rows
        } | {key.casefold() for key, _ in active_rows}
        participating = {
            str(key).casefold()
            for key, entry in snap.state.get("participating_wheels", {}).items()
            if isinstance(entry, dict)
        }
        totals_notifications = int(totals.get("preliminary_sent", 0) or 0) + int(
            totals.get("activation_sent", 0) or 0
        )
        return {
            "wheel_posts": int(totals.get("wheel_posts", 0) or 0),
            "notifications": totals_notifications,
            "sources_with_wheels": len(source_counts),
            "top_sources": sorted(
                source_counts.items(), key=lambda item: (-item[1], item[0].casefold())
            ),
            "best_day": max(day_counts.items(), key=lambda item: item[1], default=("", 0)),
            "active": len(active_rows),
            "active_with_time": sum(
                1
                for _, entry in active_rows
                if self.parse_dt(entry.get("deadline")) is not None
            ),
            "participating": len(active_keys & participating),
        }

    def show_inactive_report(self, page: int = 0) -> None:
        if not self.is_admin():
            self.send("Раздел доступен только администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot(force=True)
        rows = self.source_sets(snap)["inactive"]
        pages = max(1, math.ceil(len(rows) / INACTIVE_PAGE_SIZE))
        page = max(0, min(int(page), pages - 1))
        part = rows[page * INACTIVE_PAGE_SIZE : (page + 1) * INACTIVE_PAGE_SIZE]
        stats = snap.stats.get("sources", {})
        lines = [f"📭 <b>Давно без колёс: {len(rows)}</b>"]
        if pages > 1:
            lines.append(f"Страница: <b>{page + 1} из {pages}</b>")
        lines.append("")
        now = datetime.now(UTC)
        for source in part:
            entry = stats.get(source, {}) if isinstance(stats.get(source), dict) else {}
            reference = self.parse_dt(
                entry.get("last_wheel_post_at") or entry.get("first_checked_at")
            )
            days = max(0, (now - reference.astimezone(UTC)).days) if reference else None
            lines.append(
                f"• @{html.escape(source)} — "
                f"{f'{days} дн.' if days is not None else 'нет истории'}"
            )
        if not part:
            lines.append(
                "Все основные источники недавно публиковали колёса или ещё проходят наблюдение."
            )
        buttons: list[list[dict[str, str]]] = []
        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append({"text": "◀️ Назад", "callback_data": f"page:report:inactive:{page - 1}"})
        if page < pages - 1:
            pager.append({"text": "Вперёд ▶️", "callback_data": f"page:report:inactive:{page + 1}"})
        if pager:
            buttons.append(pager)
        buttons.append([{"text": "🔄 Обновить", "callback_data": f"page:report:inactive:{page}"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

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
        buttons: list[list[dict[str, str]]] = [
            [{"text": "🔄 Обновить", "callback_data": "refresh:status"}]
        ]
        if self.is_admin():
            buttons.append([{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    def _hide_reply_keyboard(self) -> None:
        target = str(self.current_chat_id or "")
        if not target:
            return
        try:
            result = self.telegram_api(
                "sendMessage",
                {
                    "chat_id": target,
                    "text": "Компактная панель включена.",
                    "reply_markup": {"remove_keyboard": True},
                    "disable_notification": True,
                },
            )
            message_id = int((result.get("result") or {}).get("message_id") or 0)
            if message_id:
                try:
                    self.telegram_api(
                        "deleteMessage",
                        {"chat_id": target, "message_id": message_id},
                    )
                except Exception:
                    pass
        except Exception as exc:
            print(f"WARNING remove reply keyboard: {type(exc).__name__}: {exc}")

    @staticmethod
    def _telegram_error_text(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        return str(getattr(response, "text", "") or exc)

    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        text = telegram_ui.truncate_telegram_html(text)
        target = str(chat_id or self.current_chat_id or "")
        if self._remove_reply_keyboard_before_send and self._edit_message_id is None:
            self._remove_reply_keyboard_before_send = False
            self._hide_reply_keyboard()

        if self._edit_message_id is not None and target == str(
            self.current_chat_id or ""
        ):
            payload: dict[str, Any] = {
                "chat_id": target,
                "message_id": self._edit_message_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": reply_markup or {"inline_keyboard": []},
            }
            try:
                return self.telegram_api("editMessageText", payload)
            except Exception as exc:
                detail = self._telegram_error_text(exc).casefold()
                if "message is not modified" in detail:
                    return {"ok": True, "result": {"not_modified": True}}
                print(f"WARNING edit panel message: {type(exc).__name__}: {exc}")

        return super().send(text, reply_markup=reply_markup, chat_id=chat_id)

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        admin = role in {"owner", "admin"}
        title = "панель управления" if admin else "информационная панель"
        self.send(
            f"🎡 <b>BetBoom Monitor — {title}</b>\n\n"
            f"Ваш доступ: <b>{self.role_name(role)}</b>\n"
            "Панель работает в одном сообщении: кнопки ниже переключают разделы "
            "без создания новой переписки.",
            reply_markup={"inline_keyboard": self.compact_menu_rows(admin)},
        )

    def show_more(self) -> None:
        self.send(
            "⋯ <b>Дополнительные разделы</b>",
            reply_markup=self.with_nav(
                [
                    [
                        {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                        {
                            "text": "✅ Состояние системы",
                            "callback_data": "page:status",
                        },
                    ],
                ]
            ),
        )

    @staticmethod
    def source_mode_name(mode: str) -> str:
        return {
            "primary": "Основная проверка",
            "reserve": "Ночное наблюдение",
            "paused": "Временно приостановлены",
            "quiet": "Давно без колёс",
            "fast": "Основная проверка",
            "nightly": "Ночное наблюдение",
        }.get(mode, mode)

    def show_source_detail(self, source: str) -> None:
        source = self.safe_source(source)
        snap = self.snapshot()
        stats = self.merged_source_stats(snap).get(source, {})
        health = snap.health.get("sources", {}).get(source, {})
        discovery = snap.discovery.get("sources", {}).get(source, {})
        primary_set = {value.casefold() for value in snap.fast}
        nightly_set = {value.casefold() for value in snap.nightly}
        mode = (
            "Основная проверка"
            if source.casefold() in primary_set
            else (
                "Ночное наблюдение"
                if source.casefold() in nightly_set
                else "Не включён"
            )
        )
        raw_status = str(health.get("status") or discovery.get("status") or "unknown")
        failure_reason = str(
            health.get("failure_reason") or health.get("last_error") or ""
        ).strip()
        wheels = self.counter(stats, "wheel_posts") or self.counter(
            discovery, "wheel_links_found"
        )
        score = int(stats.get("quality_score", 0) or 0)
        reason_line = (
            f"Причина: {html.escape(failure_reason[:180])}\n"
            if failure_reason
            else ""
        )
        text = (
            f"📡 <b>@{html.escape(source)}</b>\n\n"
            f"Проверяется: <b>{mode}</b>\n"
            f"Состояние: {html.escape(self.source_status_name(raw_status))}\n"
            f"{reason_line}"
            f"Проверок: {self.counter(stats, 'checks')}\n"
            f"Постов с колёсами: {wheels}\n"
            f"Очки рейтинга: {score}\n"
            f"Последнее колесо: "
            f"{self.fmt_dt(stats.get('last_wheel_post_at') or discovery.get('latest_wheel_at'))}\n"
            f"Последняя проверка: "
            f"{self.fmt_dt(health.get('last_checked_at') or discovery.get('checked_at'))}"
        )
        rows: list[list[dict[str, Any]]] = [
            [{"text": "Открыть Telegram", "url": f"https://telegram.me/{source}"}]
        ]
        if self.is_admin():
            move: list[dict[str, str]] = []
            if mode != "Основная проверка":
                move.append(
                    {
                        "text": "⚡ В основные",
                        "callback_data": f"source:move:fast:{source}",
                    }
                )
            if mode != "Ночное наблюдение":
                move.append(
                    {
                        "text": "🌙 В ночное наблюдение",
                        "callback_data": f"source:move:nightly:{source}",
                    }
                )
            if move:
                rows.append(move)
            if raw_status == "quarantined":
                rows.append(
                    [
                        {
                            "text": "▶️ Возобновить проверки",
                            "callback_data": f"source:clearq:{source}",
                        }
                    ]
                )
            rows.append(
                [
                    {
                        "text": "🗑 Удалить",
                        "callback_data": f"source:removeask:{source}",
                    }
                ]
            )
        self.send(text, reply_markup=self.with_nav(rows))

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot()
        participating = {
            str(key).casefold()
            for key, entry in snap.state.get("participating_wheels", {}).items()
            if isinstance(entry, dict)
        }
        if not items:
            self.send(
                "🔥 <b>Действующих колёс сейчас нет.</b>",
                reply_markup=self.with_nav(
                    [
                        [
                            {
                                "text": "🔄 Обновить список",
                                "callback_data": "refresh:active",
                            }
                        ]
                    ]
                ),
            )
            return

        lines = [f"🔥 <b>Действующие колёса: {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier)
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            participates = (
                identifier.casefold() in participating or key.casefold() in participating
            )
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                    f"⏳ {html.escape(self.remaining(deadline) if deadline else 'время не определено')}",
                    f"📡 @{html.escape(source)}",
                    "✅ Участие отмечено" if participates else "❌ Участие не отмечено",
                    "",
                ]
            )
            row: list[dict[str, str]] = []
            url = str(item.get("url") or "")
            if url:
                row.append({"text": "🎡 Открыть колесо", "url": url})
            if not participates:
                row.append(
                    {"text": "✅ Я участвую", "callback_data": f"wheel:part:{key}"}
                )
            if row:
                buttons.append(row)
            if self.is_admin():
                buttons.append(
                    [
                        {
                            "text": "🗑 Убрать из списка",
                            "callback_data": f"wheel:removeask:{key}",
                        }
                    ]
                )
        buttons.append(
            [{"text": "🔄 Обновить список", "callback_data": "refresh:active"}]
        )
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def bulk_intelligence_rows(
        self, category: str
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self.filtered_intelligence_rows(category)
        public_rows = [row for row in rows if row.get("public") is True]
        return public_rows, max(0, len(rows) - len(public_rows))

    def show_intelligence(self) -> None:
        if not self.is_admin():
            self.send(
                "Этот раздел доступен администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        state = self.intelligence_state()
        summary = (
            state.get("last_run_summary")
            if isinstance(state.get("last_run_summary"), dict)
            else {}
        )
        rows = self.intelligence_rows()
        new_rows = [
            row
            for row in rows
            if row.get("decision") == "new"
            and self.intelligence_row_is_relevant(row)
        ]
        wheel_rows = [
            row
            for row in new_rows
            if int(row.get("wheel_links_found", 0) or 0) > 0
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
            f"Просканировано каналов: "
            f"<b>{int(summary.get('sources_scanned', 0) or 0)}</b>\n"
            f"Новых кандидатов: <b>{len(new_rows)}</b>\n"
            f"С найденными колёсами: <b>{len(wheel_rows)}</b>\n\n"
            "Разведка учитывает только тематические ссылки и упоминания внутри "
            "известных источников. Боты и обычные нетематические упоминания "
            "отбрасываются; подтверждённые кандидаты сначала идут в ночную "
            "проверку."
        )
        buttons = [
            [{
                "text": f"🆕 Новые находки ({len(new_rows)})",
                "callback_data": "intel:list:new:0",
            }],
            [{
                "text": f"🎡 С колёсами ({len(wheel_rows)})",
                "callback_data": "intel:list:wheels:0",
            }],
            [{
                "text": "▶️ Запустить разведку",
                "callback_data": "control:intelligence",
            }],
            [{
                "text": "🔄 Обновить состояние",
                "callback_data": "page:intelligence",
            }],
        ]
        self.send(text, reply_markup=self.with_nav(buttons))

    def show_intelligence_list(self, category: str, page: int = 0) -> None:
        if not self.is_admin():
            self.send(
                "Этот раздел доступен администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        rows = self.filtered_intelligence_rows(category)
        max_page = max(0, (len(rows) - 1) // INTELLIGENCE_PER_PAGE)
        page = max(0, min(page, max_page))
        part = rows[
            page * INTELLIGENCE_PER_PAGE : (page + 1) * INTELLIGENCE_PER_PAGE
        ]
        titles = {
            "new": "Новые источники из Telegram-сети",
            "wheels": "Новые источники с найденными колёсами",
            "ignored": "Игнорируемые находки",
            "all": "Все результаты разведки",
        }
        lines = [
            f"🛰️ <b>{html.escape(titles.get(category, 'Результаты разведки'))}</b>",
            f"Страница {page + 1} из {max_page + 1}",
            "",
        ]
        buttons: list[list[dict[str, str]]] = []
        for item in part:
            source = str(item.get("source") or "")
            score = int(item.get("score", 0) or 0)
            wheels = int(item.get("wheel_links_found", 0) or 0)
            refs = (
                len(item.get("discovered_from", []))
                if isinstance(item.get("discovered_from"), list)
                else 0
            )
            lines.extend(
                [
                    f"<b>@{html.escape(source)}</b>",
                    f"{self.intelligence_label(score, wheels)} · оценка {score}/100",
                    f"Связей: {refs} · упоминаний: "
                    f"{int(item.get('mention_count', 0) or 0)} · колёс: {wheels}",
                    "",
                ]
            )
            buttons.append(
                [
                    {
                        "text": f"@{source[:25]} · {score}",
                        "callback_data": f"intel:detail:{source}",
                    }
                ]
            )
        if not part:
            lines.append("Список пуст.")

        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append(
                {
                    "text": "◀️",
                    "callback_data": f"intel:list:{category}:{page - 1}",
                }
            )
        if page < max_page:
            nav.append(
                {
                    "text": "▶️",
                    "callback_data": f"intel:list:{category}:{page + 1}",
                }
            )
        if nav:
            buttons.append(nav)

        bulk_rows, skipped = self.bulk_intelligence_rows(category)
        if category in {"new", "wheels"} and bulk_rows:
            buttons.extend(
                [
                    [
                        {
                            "text": f"⚡ Все в основные ({len(bulk_rows)})",
                            "callback_data": f"intel:bulkask:fast:{category}",
                        }
                    ],
                    [
                        {
                            "text": f"🌙 Все в ночное наблюдение ({len(bulk_rows)})",
                            "callback_data": f"intel:bulkask:nightly:{category}",
                        }
                    ],
                ]
            )
            if skipped:
                lines.append(
                    f"\nНе подтверждены как публичные и не войдут в групповое "
                    f"действие: {skipped}."
                )
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def show_intelligence_detail(self, source: str) -> None:
        if not self.is_admin():
            self.send(
                "Этот раздел доступен администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        source = self.safe_source(source)
        item = next(
            (
                row
                for row in self.intelligence_rows()
                if str(row.get("source") or "").casefold() == source.casefold()
            ),
            None,
        )
        if item is None:
            self.send(
                "Результат разведки больше не найден.",
                reply_markup=self.with_nav(),
            )
            return
        score = int(item.get("score", 0) or 0)
        wheels = int(item.get("wheel_links_found", 0) or 0)
        discovered_from = (
            item.get("discovered_from", [])
            if isinstance(item.get("discovered_from"), list)
            else []
        )
        signals = sorted(
            {
                str(value)
                for field in (
                    item.get("context_signals", []),
                    item.get("candidate_signals", []),
                    item.get("username_signals", []),
                )
                if isinstance(field, list)
                for value in field
                if str(value)
            }
        )
        lines = [
            f"🛰️ <b>@{html.escape(source)}</b>",
            "",
            f"Оценка: <b>{score}/100</b> — {self.intelligence_label(score, wheels)}",
            f"Публичный канал: {'✅ да' if item.get('public') else '❌ не подтверждён'}",
            f"Найдено упоминаний: {int(item.get('mention_count', 0) or 0)}",
            f"Найдено колёс: {wheels}",
            f"Просмотрено сообщений при проверке: "
            f"{int(item.get('messages_checked', 0) or 0)}",
            f"Последнее найденное колесо: {self.fmt_dt(item.get('latest_wheel_at'))}",
            f"Последняя проверка: {self.fmt_dt(item.get('last_verified_at'))}",
            "Тематические признаки: "
            + (", ".join(html.escape(value) for value in signals) if signals else "не сохранены"),
            "",
            "<b>Откуда найден</b>",
        ]
        lines.extend(f"• @{html.escape(str(name))}" for name in discovered_from[:12])
        if not discovered_from:
            lines.append("• источник связи не сохранён")
        samples = (
            item.get("sample_wheels", [])
            if isinstance(item.get("sample_wheels"), list)
            else []
        )
        if samples:
            lines.extend(["", "<b>Примеры колёс</b>"])
            for sample in samples[:5]:
                if not isinstance(sample, dict):
                    continue
                identifier = html.escape(str(sample.get("identifier") or "колесо"))
                lines.append(
                    f"• <code>{identifier}</code> — "
                    f"{self.fmt_dt(sample.get('published_at'))}"
                )
        buttons: list[list[dict[str, str]]] = [
            [{"text": "📨 Открыть канал", "url": f"https://telegram.me/{source}"}]
        ]
        if item.get("decision") != "known":
            buttons.extend(
                [
                    [{
                        "text": "⚡ В основную проверку",
                        "callback_data": f"intel:mode:fast:{source}",
                    }],
                    [{
                        "text": "🌙 В ночное наблюдение",
                        "callback_data": f"intel:mode:nightly:{source}",
                    }],
                ]
            )
        if item.get("decision") == "ignored":
            buttons.append([{
                "text": "↩️ Вернуть в ночное наблюдение",
                "callback_data": f"intel:restore:{source}",
            }])
        elif item.get("decision") != "known":
            buttons.append([{
                "text": "🙈 Игнорировать",
                "callback_data": f"intel:ignoreask:{source}",
            }])
        buttons.append(
            [{"text": "🛰️ К результатам", "callback_data": "page:intelligence"}]
        )
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    def set_candidate_mode(self, source: str, mode: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        available, detail = self.verify_public_source(source)
        if not available:
            raise ValueError(detail)
        moderation = self.load_moderation()
        moderation["ignored"].pop(source.casefold(), None)
        self.save_moderation(
            moderation,
            f"Approve @{source} discovery candidate via Telegram [skip ci]",
        )
        self.set_source_mode(source, mode)
        if mode == "nightly":
            return (
                f"@{source} добавлен в ночную проверку. "
                "Первая проверка пройдёт по ночному расписанию."
            )
        return f"@{source} добавлен в основную проверку."

    def ignore_candidate(self, source: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        self.set_source_mode(source, "remove")
        moderation = self.load_moderation()
        moderation["ignored"][source.casefold()] = {
            "source": source,
            "ignored_at": datetime.now(UTC).isoformat(),
            "ignored_by": "admin",
        }
        self.save_moderation(
            moderation,
            f"Ignore @{source} discovery candidate via Telegram [skip ci]",
        )
        return f"@{source} исключён из поиска и скрыт из очереди."

    def restore_candidate(self, source: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        moderation = self.load_moderation()
        moderation["ignored"].pop(source.casefold(), None)
        self.save_moderation(
            moderation,
            f"Restore @{source} discovery candidate via Telegram [skip ci]",
        )
        self.set_source_mode(source, "nightly")
        return (
            f"@{source} возвращён в ночную проверку. "
            "Следующая проверка пройдёт по ночному расписанию."
        )

    @staticmethod
    def _write_source_list(header: str, values: list[str]) -> str:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip().lstrip("@")
            key = value.casefold()
            if value and key not in seen:
                result.append(value)
                seen.add(key)
        return header.rstrip() + "\n\n" + "\n".join(result) + "\n"

    def bulk_set_intelligence_mode(self, category: str, mode: str) -> tuple[int, int]:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        if mode not in {"fast", "nightly"}:
            raise ValueError("Неизвестный режим")
        rows, skipped = self.bulk_intelligence_rows(category)
        targets = [
            str(row.get("source") or "").strip().lstrip("@") for row in rows
        ]
        targets = [value for value in targets if value]
        if not targets:
            return 0, skipped

        fast_text, _ = self.get_file("public_sources.txt")
        nightly_text, _ = self.get_file("source_catalog.txt")
        fast = self.parse_list(fast_text)
        nightly = self.parse_list(nightly_text)
        target_keys = {value.casefold() for value in targets}
        fast = [value for value in fast if value.casefold() not in target_keys]
        nightly = [value for value in nightly if value.casefold() not in target_keys]
        if mode == "fast":
            fast.extend(targets)
        else:
            nightly.extend(targets)

        fast_new = self._write_source_list(
            "# Основной мониторинг: отобранные тематические источники в 7-дневном наблюдении.\n"
            "# Проверяется с интервалом, выбранным в настройках Telegram-панели.\n"
            "# Автоматический перенос в ночную проверку возможен только после 7 "
            "полных дней наблюдения без новых колёс.",
            fast,
        )
        nightly_new = self._write_source_list(
            "# Ночное наблюдение: резервные источники и кандидаты.\n"
            "# Возврат в основную проверку выполняется администратором.",
            nightly,
        )
        if fast_new != fast_text:
            self.update_file(
                "public_sources.txt",
                fast_new,
                f"Bulk move intelligence candidates to {mode} via Telegram",
            )
        if nightly_new != nightly_text:
            self.update_file(
                "source_catalog.txt",
                nightly_new,
                f"Bulk move intelligence candidates to {mode} via Telegram",
            )
        self.cache = None
        self.dispatch("monitor.yml", {"continuous": "true"})
        return len(targets), skipped

    def pending_rows(self, snap: Any) -> list[tuple[str, dict[str, Any]]]:
        now = datetime.now(UTC)
        rows: list[tuple[str, dict[str, Any]]] = []
        for key, entry in snap.state.get("pending_posts", {}).items():
            if not isinstance(entry, dict):
                continue
            expires = self.parse_dt(entry.get("expires_at"))
            if expires is not None and expires.astimezone(UTC) < now:
                continue
            rows.append((str(key), entry))
        rows.sort(
            key=lambda item: (
                self.parse_dt(item[1].get("first_seen_at"))
                or datetime.max.replace(tzinfo=UTC),
                str(item[1].get("identifier") or item[0]).casefold(),
            )
        )
        return rows

    @staticmethod
    def pending_reason(entry: dict[str, Any], active_identifiers: set[str]) -> str:
        identifier = str(entry.get("identifier") or "").casefold()
        if identifier and identifier in active_identifiers:
            return (
                "уже показано как действующее; запись сохраняется для контроля "
                "до дедлайна"
            )
        status = str(entry.get("status") or "")
        if status == "telegram_deadline":
            return (
                "время найдено в сообщении Telegram; монитор следит до указанного "
                "срока"
            )
        reason = str(entry.get("reason") or "").strip()
        return reason or "ссылка найдена и ожидает очередной проверки"

    def show_pending(self) -> None:
        snap = self.snapshot()
        rows = self.pending_rows(snap)
        active_identifiers = {
            str(entry.get("identifier") or key).casefold()
            for key, entry in snap.state.get("active_wheels", {}).items()
            if isinstance(entry, dict)
        }
        lines = [f"🔎 <b>Колёса на перепроверке: {len(rows)}</b>", ""]
        buttons: list[list[dict[str, Any]]] = []
        for index, (key, entry) in enumerate(rows[:20], 1):
            identifier = str(entry.get("identifier") or key)
            source = str(entry.get("source") or "неизвестно")
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                    f"Канал: @{html.escape(source)}",
                    f"Причина: {html.escape(self.pending_reason(entry, active_identifiers))}",
                    f"Последняя проверка: {self.fmt_dt(entry.get('last_checked_at'))}",
                    f"Хранить до: {self.fmt_dt(entry.get('expires_at'))}",
                    "",
                ]
            )
            row: list[dict[str, Any]] = []
            if entry.get("message_url"):
                row.append(
                    {"text": f"📨 Пост {index}", "url": str(entry["message_url"])}
                )
            if entry.get("url"):
                row.append({"text": f"🎡 Колесо {index}", "url": str(entry["url"])})
            if row:
                buttons.append(row)
        if not rows:
            lines.append("Ссылок, ожидающих автоматической перепроверки, сейчас нет.")
        buttons.append([{"text": "🔄 Обновить", "callback_data": "refresh:pending"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot()
        totals = self.period_totals(snap.stats, days)
        pending = self.pending_rows(snap)
        title = "сегодня" if days == 1 else f"за {days} дней"
        text = (
            f"📊 <b>Статистика {title}</b>\n\n"
            f"Проверок источников: {totals.get('checks', 0)}\n"
            f"Просмотрено сообщений: {totals.get('messages_scanned', 0)}\n"
            f"Найдено постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Отправлено первых уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Подтверждено активных колёс: {totals.get('activation_sent', 0)}\n"
            f"Повторные уведомления подавлены: "
            f"{totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок проверки: {totals.get('errors', 0)}\n\n"
            f"Сейчас действующих колёс: {len(self._collect_current_wheels())}\n"
            f"Колёс на перепроверке: {len(pending)}"
        )
        rows: list[list[dict[str, str]]] = [
            [
                {"text": "Сегодня", "callback_data": "page:stats:1"},
                {"text": "7 дней", "callback_data": "page:stats:7"},
                {"text": "30 дней", "callback_data": "page:stats:30"},
            ]
        ]
        if pending:
            rows.append(
                [
                    {
                        "text": f"🔎 На перепроверке ({len(pending)})",
                        "callback_data": "page:pending",
                    }
                ]
            )
        rows.extend(
            [
                [
                    {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
                    {
                        "text": "📭 Давно без колёс",
                        "callback_data": "page:report:inactive",
                    },
                ],
                [
                    {
                        "text": "⚠️ Ошибки источников",
                        "callback_data": "page:report:errors",
                    }
                ],
            ]
        )
        if self.is_admin():
            rows.append(
                [
                    {
                        "text": "📨 Отправить ежедневную сводку",
                        "callback_data": "control:daily",
                    }
                ]
            )
        self.send(text, reply_markup=self.with_nav(rows))

    def render_page(self, page: str) -> None:
        if page == "intelligence":
            self.show_intelligence()
            return
        if page.startswith("intel_list:"):
            _, category, page_no = page.split(":", 2)
            self.show_intelligence_list(category, int(page_no))
            return
        if page.startswith("intel_detail:"):
            self.show_intelligence_detail(page.split(":", 1)[1])
            return
        if page == "more":
            self.show_menu(clear_stack=True)
            return
        if page == "pending":
            self.show_pending()
            return
        if page == "reports":
            self.show_stats(1)
            return
        super().render_page(page)

    def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        command = (
            text.split("@", 1)[0].split(maxsplit=1)[0].casefold() if text else ""
        )
        legacy_buttons = {
            "📊 Статистика",
            "🔥 Активные колёса",
            "📡 Источники",
            "🏆 Рейтинг каналов",
            "📅 Отчёты",
            "🌙 Ночное наблюдение",
            "🛰️ Разведка источников",
            "⚙️ Настройки",
            "📱 Приложение",
            "✅ Проверка работы",
            "🛠 Управление",
            "🏠 Главное меню",
        }
        if command in {"/start", "/menu"} or text in legacy_buttons:
            self._remove_reply_keyboard_before_send = True
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        message = query.get("message") or {}
        message_id = int(message.get("message_id") or 0)
        self._edit_message_id = message_id or None
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        self.set_context(chat.get("id"), sender.get("id"))
        try:
            if data.startswith("intel:list:"):
                if not self.is_admin():
                    raise PermissionError
                _, _, category, page_no = data.split(":", 3)
                self.answer(query_id, "Открываю")
                self.open_page(f"intel_list:{category}:{page_no}")
                return
            if data.startswith("intel:detail:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                self.answer(query_id, "Открываю")
                self.open_page(f"intel_detail:{source}")
                return
            if data.startswith("intel:mode:"):
                if not self.is_admin():
                    raise PermissionError
                _, _, mode, source = data.split(":", 3)
                result = self.set_candidate_mode(source, mode)
                self.answer(query_id, "Добавлено")
                self.refresh_snapshot()
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("intel:ignoreask:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Игнорировать @{html.escape(source)}? Канал будет исключён "
                    "из дальнейшей разведки.",
                    reply_markup=self.with_nav(
                        [[
                            {
                                "text": "Да, игнорировать",
                                "callback_data": f"intel:ignore:{source}",
                            },
                            {
                                "text": "Отмена",
                                "callback_data": f"intel:detail:{source}",
                            },
                        ]]
                    ),
                )
                return
            if data.startswith("intel:ignore:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                result = self.ignore_candidate(source)
                self.answer(query_id, "Скрыто")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("intel:restore:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                result = self.restore_candidate(source)
                self.answer(query_id, "Возвращено")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("intel:bulkask:"):
                _, _, mode, category = data.split(":", 3)
                if not self.is_admin():
                    raise PermissionError
                rows, skipped = self.bulk_intelligence_rows(category)
                mode_text = (
                    "основную проверку" if mode == "fast" else "ночное наблюдение"
                )
                self.answer(query_id, "Нужно подтверждение")
                self.send(
                    f"Подтвердить перенос <b>{len(rows)}</b> публичных каналов "
                    f"в {mode_text}?"
                    + (
                        f"\nНе подтверждено публичных: {skipped}."
                        if skipped
                        else ""
                    ),
                    reply_markup=self.with_nav(
                        [
                            [
                                {
                                    "text": "Да, перенести все",
                                    "callback_data": f"intel:bulk:{mode}:{category}",
                                }
                            ],
                            [
                                {
                                    "text": "Отмена",
                                    "callback_data": f"page:intel_list:{category}:0",
                                }
                            ],
                        ]
                    ),
                )
                return
            if data.startswith("intel:bulk:"):
                _, _, mode, category = data.split(":", 3)
                moved, skipped = self.bulk_set_intelligence_mode(category, mode)
                self.answer(query_id, "Готово")
                mode_text = (
                    "основную проверку" if mode == "fast" else "ночное наблюдение"
                )
                self.refresh_snapshot()
                self.send(
                    f"✅ В {mode_text} перенесено: <b>{moved}</b>."
                    + (
                        f"\nПропущено неподтверждённых публичных: {skipped}."
                        if skipped
                        else ""
                    ),
                    reply_markup=self.with_nav(
                        [
                            [
                                {
                                    "text": "🛰️ К разведке",
                                    "callback_data": "page:intelligence",
                                }
                            ]
                        ]
                    ),
                )
                return
            super().handle_callback(query)
        except PermissionError:
            self.answer(query_id, "Недостаточно прав")
        except Exception as exc:
            self.answer(query_id, "Ошибка")
            self.send(
                f"⚠️ Ошибка: <code>{html.escape(type(exc).__name__)}</code>.",
                reply_markup=self.with_nav(),
            )
        finally:
            self._edit_message_id = None


def self_test() -> None:
    admin_rows = PanelInterfaceRuntime.compact_menu_rows(True)
    callbacks = [button.get("callback_data") for row in admin_rows for button in row]
    expected = {
        "page:active",
        "page:analytics",
        "page:sources",
        "page:settings",
        "page:control",
    }
    assert set(callbacks) == expected
    assert "page:more" not in callbacks
    assert "page:reports" not in callbacks
    assert all(len(row) <= 2 for row in admin_rows)
    assert PanelInterfaceRuntime.source_mode_name("nightly") == "Ночное наблюдение"
    assert "прокрутка впереди" not in PanelInterfaceRuntime.show_active.__code__.co_consts
    assert any(
        isinstance(value, str) and "Участие отмечено" in value
        for value in PanelInterfaceRuntime.show_active.__code__.co_consts
    )
    assert "Отчёты" not in str(PanelInterfaceRuntime.show_more.__code__.co_consts)
    assert "Причина:" in str(PanelInterfaceRuntime.show_pending.__code__.co_consts)

    panel = object.__new__(PanelInterfaceRuntime)
    panel.parse_dt = lambda value: None  # type: ignore[method-assign]
    snap = SimpleNamespace(
        state={
            "pending_posts": {
                "wheel-b": {
                    "identifier": "wheel-b",
                    "first_seen_at": "2026-07-16T10:00:00+00:00",
                },
                "wheel-a": {
                    "identifier": "wheel-a",
                    "first_seen_at": "2026-07-16T09:00:00+00:00",
                },
            }
        }
    )
    rows = panel.pending_rows(snap)
    assert [key for key, _ in rows] == ["wheel-a", "wheel-b"]
    assert (
        panel.pending_reason(
            {"identifier": "wheel-a", "status": "telegram_deadline"},
            set(),
        )
        == "время найдено в сообщении Telegram; монитор следит до указанного срока"
    )
    assert PanelInterfaceRuntime._write_source_list(
        "# header", ["@One", "one", "Two"]
    ) == "# header\n\nOne\nTwo\n"
    print("BB V.G. panel interface self-test passed")


if __name__ == "__main__":
    self_test()
