from __future__ import annotations

import argparse
import hashlib
import html
import math
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import admin_bot as legacy
import telegram_ui
from admin_panel_runtime_v37 import TelegramPanelRuntimeV37


UTC = timezone.utc
ACTIVE_PAGE_SIZE = 6
ACCESS_PAGE_SIZE = 8
INACTIVE_PAGE_SIZE = 10
_SAFE_CALLBACK_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.~-]+$")


class TelegramPanelRuntimeV38(TelegramPanelRuntimeV37):
    """Chapter 4: compact, role-safe and internally consistent Telegram UI."""

    RUNTIME_VERSION = 38

    # ---------- Shared UI guarantees ----------
    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        return super().send(
            telegram_ui.truncate_telegram_html(text),
            reply_markup=reply_markup,
            chat_id=chat_id,
        )

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
        if admin:
            rows.append([{"text": "🛠 Управление", "callback_data": "page:control"}])
        else:
            rows.append(
                [{"text": "✅ Работа системы", "callback_data": "page:status"}]
            )
        return rows

    @staticmethod
    def _normalize_page(page: str) -> str:
        value = str(page or "menu")
        if value == "analytics" or value == "reports":
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

    @staticmethod
    def _page_family(page: str) -> str:
        value = TelegramPanelRuntimeV38._normalize_page(page)
        for prefix in ("analytics:", "active:", "access:", "recipients:"):
            if value.startswith(prefix):
                return prefix.rstrip(":")
        for prefix in ("source_list:", "candidate_list:", "intel_list:"):
            if value.startswith(prefix):
                parts = value.split(":")
                return ":".join(parts[:2])
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

    @staticmethod
    def _wheel_digest(key: str) -> str:
        normalized = str(key or "").casefold()
        return "~" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]

    @classmethod
    def _wheel_token(cls, key: str, available_bytes: int) -> str:
        normalized = str(key or "").casefold()
        if (
            normalized
            and _SAFE_CALLBACK_TOKEN_RE.fullmatch(normalized)
            and len(normalized.encode("utf-8")) <= available_bytes
        ):
            return normalized
        return cls._wheel_digest(normalized)

    @classmethod
    def _wheel_callback(cls, action: str, key: str) -> str:
        prefix = f"wheel:{action}:"
        token = cls._wheel_token(
            key, telegram_ui.TELEGRAM_CALLBACK_LIMIT - len(prefix.encode("utf-8"))
        )
        return prefix + token

    @classmethod
    def _quick_time_callback(cls, key: str, minutes: int) -> str:
        prefix = "wheel:timequick:"
        suffix = f":{minutes}"
        available = (
            telegram_ui.TELEGRAM_CALLBACK_LIMIT
            - len(prefix.encode("utf-8"))
            - len(suffix.encode("utf-8"))
        )
        return prefix + cls._wheel_token(key, available) + suffix

    def _resolve_wheel_token(self, token: str) -> str | None:
        value = str(token or "")
        if not value.startswith("~"):
            return value.casefold()
        matches: list[str] = []
        for item in self._collect_current_wheels():
            key = str(item.get("_key") or item.get("identifier") or "").casefold()
            if key and self._wheel_digest(key) == value:
                matches.append(key)
        return matches[0] if len(set(matches)) == 1 else None

    # ---------- Active wheels ----------
    def show_active(self, page: int = 0) -> None:
        snap = self.snapshot(force=True)
        items = self._collect_current_wheels()
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
                    [[{"text": "🔄 Обновить", "callback_data": "refresh:active:0"}]]
                ),
            )
            return

        pages = max(1, math.ceil(len(items) / ACTIVE_PAGE_SIZE))
        page = max(0, min(int(page), pages - 1))
        start = page * ACTIVE_PAGE_SIZE
        visible = items[start : start + ACTIVE_PAGE_SIZE]
        admin = self.is_admin()
        lines = [
            f"🔥 <b>Активные колёса: {len(items)}</b>",
            f"Состояние: {state_line}",
        ]
        if pages > 1:
            lines.append(f"Страница: <b>{page + 1} из {pages}</b>")
        lines.append("")
        buttons: list[list[dict[str, str]]] = []

        for offset, item in enumerate(visible):
            index = start + offset + 1
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier).casefold()
            deadline = self.parse_dt(item.get("deadline"))
            available_at = self.parse_dt(item.get("available_at"))
            joined = identifier.casefold() in participating or key in participating
            sources = self._sources_for_item(snap, key, item)
            shown_sources = sources[:3]
            source_text = ", ".join(f"@{source}" for source in shown_sources)
            if len(sources) > len(shown_sources):
                source_text += f" и ещё {len(sources) - len(shown_sources)}"
            source_text = source_text or "источник неизвестен"
            if available_at and available_at > datetime.now(UTC):
                timing = f"🟡 Участие откроется через {self.remaining(available_at)}"
            elif available_at and item.get("availability_status") == "available":
                timing = "🟢 Доступно сейчас · 🔴 время прокрутки неизвестно"
            else:
                timing = self.remaining(deadline) if deadline else "🔴 Время прокрутки неизвестно"
            joined_text = "✅ участвуете" if joined else "❌ участие не отмечено"
            shown_identifier = identifier if len(identifier) <= 90 else identifier[:87] + "…"
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(shown_identifier)}</code></b>",
                    f"{html.escape(timing)}",
                    f"📡 {html.escape(source_text)} · {joined_text}",
                    "",
                ]
            )

            url = str(item.get("url") or "")
            if url:
                buttons.append(
                    [{"text": f"🎡 {index} · Открыть колесо", "url": url}]
                )
            if not joined:
                buttons.append(
                    [
                        {
                            "text": f"✅ {index} · Участвую",
                            "callback_data": self._wheel_callback("part", key),
                        }
                    ]
                )
            if admin:
                buttons.append(
                    [
                        {
                            "text": f"🏁 {index} · Завершено",
                            "callback_data": self._wheel_callback("finished", key),
                        },
                        {
                            "text": f"🚫 {index} · Неактивное",
                            "callback_data": self._wheel_callback("inactive", key),
                        },
                    ]
                )
                time_label = "Изменить время" if deadline else "Указать время"
                buttons.append(
                    [
                        {
                            "text": f"⏱ {index} · {time_label}",
                            "callback_data": self._wheel_callback("time", key),
                        }
                    ]
                )
            else:
                buttons.append(
                    [
                        {
                            "text": f"🙈 {index} · Скрыть у меня",
                            "callback_data": self._wheel_callback("inactive", key),
                        }
                    ]
                )

        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append(
                {"text": "◀️ Назад", "callback_data": f"page:active:{page - 1}"}
            )
        if page < pages - 1:
            pager.append(
                {"text": "Вперёд ▶️", "callback_data": f"page:active:{page + 1}"}
            )
        if pager:
            buttons.append(pager)
        buttons.append(
            [{"text": "🔄 Обновить", "callback_data": f"refresh:active:{page}"}]
        )
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def request_manual_time(self, key: str) -> None:
        if not self.is_admin():
            raise PermissionError("Только администратор может задавать время")
        normalized = str(key).casefold()
        self.pending_input[int(self.current_user_id or 0)] = {
            "kind": "wheel_time",
            "key": normalized,
        }
        quick_values = ((15, 30, 45), (60, 75, 90), (120, 150, 180), (240, 360, 720))
        labels = {
            15: "+15 мин",
            30: "+30 мин",
            45: "+45 мин",
            60: "+1 час",
            75: "+1:15",
            90: "+1:30",
            120: "+2 часа",
            150: "+2:30",
            180: "+3 часа",
            240: "+4 часа",
            360: "+6 часов",
            720: "+12 часов",
        }
        quick = [
            [
                {
                    "text": labels[minutes],
                    "callback_data": self._quick_time_callback(normalized, minutes),
                }
                for minutes in row
            ]
            for row in quick_values
        ]
        shown = normalized if len(normalized) <= 100 else normalized[:97] + "…"
        self.send(
            f"⏱ <b>Укажите время для <code>{html.escape(shown)}</code></b>\n\n"
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
        resolved = self._resolve_wheel_token(key)
        if not resolved:
            raise ValueError("Колесо больше не активно")
        return super()._set_quick_time(resolved, minutes)

    # ---------- Analytics and rating ----------
    def period_overview(self, snap: Any, days: int) -> dict[str, Any]:
        result = super().period_overview(snap, days)
        current = self._collect_current_wheels()
        participating = self._joined_wheel_keys(snap)
        result["active"] = len(current)
        result["active_with_time"] = sum(
            1 for item in current if self.parse_dt(item.get("deadline")) is not None
        )
        result["participating"] = sum(
            1
            for item in current
            if str(item.get("_key") or "").casefold() in participating
            or str(item.get("identifier") or "").casefold() in participating
        )
        return result

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

    def show_analytics(self, days: int = 1) -> None:
        days = days if days in {1, 7, 30} else 1
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        lines = [f"📊 <b>Аналитика {self.period_title(days)}</b>", "", "<b>За период</b>"]
        if overview["wheel_posts"]:
            lines.extend(
                [
                    f"🎡 Постов с колёсами: <b>{overview['wheel_posts']}</b>",
                    f"📡 Каналов с находками: <b>{overview['sources_with_wheels']}</b>",
                ]
            )
            if days > 1:
                lines.append(
                    f"📈 В среднем постов за день: <b>{overview['wheel_posts'] / days:.1f}</b>"
                )
            if overview["top_sources"]:
                lines.extend(["", "<b>Топ каналов по найденным постам</b>"])
                for index, (source, count) in enumerate(overview["top_sources"][:5], 1):
                    lines.append(f"{index}. @{html.escape(source)} — {count}")
        else:
            lines.append("Новых постов с колёсами не найдено.")

        lines.extend(["", "<b>Сейчас</b>"])
        lines.append(f"🔥 Активных колёс: <b>{overview['active']}</b>")
        if overview["active"]:
            lines.append(
                f"⏱ С указанным временем: <b>{overview['active_with_time']} из {overview['active']}</b>"
            )
            lines.append(
                f"✅ С вашей отметкой: <b>{overview['participating']} из {overview['active']}</b>"
            )

        lines.extend(
            [
                "",
                "Один пост учитывается один раз. Если одно колесо разместили разные "
                "каналы, постов будет несколько, а уведомление останется одним.",
            ]
        )
        rows: list[list[dict[str, str]]] = [self._period_buttons(days)]
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

    def show_ranking(self) -> None:
        rows = self.ranked_sources(self.snapshot(force=True).stats)
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            "Очки меняются только после решения администратора: подтверждённое "
            "колесо даёт +40 очков, а отметка «Неактивное» отменяет эти очки.",
            "",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score, _confirmed) in enumerate(rows, 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{mark} <b>@{html.escape(source)}</b> — <b>{score}</b> оч.")
        if not rows:
            lines.append("Рейтинг пока пуст. Он появится после решений администратора.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить", "callback_data": "page:ranking"}]]
            ),
        )

    def show_inactive_report(self, page: int = 0) -> None:
        if not self.is_admin():
            self.send(
                "Раздел доступен только администраторам.",
                reply_markup=self.with_nav(),
            )
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
            days = (
                max(0, (now - reference.astimezone(UTC)).days) if reference else None
            )
            detail = f"{days} дн." if days is not None else "нет истории"
            lines.append(f"• @{html.escape(source)} — {detail}")
        if not part:
            lines.append(
                "Все основные источники недавно публиковали колёса или ещё проходят наблюдение."
            )
        buttons: list[list[dict[str, str]]] = []
        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append(
                {
                    "text": "◀️ Назад",
                    "callback_data": f"page:report:inactive:{page - 1}",
                }
            )
        if page < pages - 1:
            pager.append(
                {
                    "text": "Вперёд ▶️",
                    "callback_data": f"page:report:inactive:{page + 1}",
                }
            )
        if pager:
            buttons.append(pager)
        buttons.append(
            [
                {
                    "text": "🔄 Обновить",
                    "callback_data": f"page:report:inactive:{page}",
                }
            ]
        )
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    # ---------- Status and long owner lists ----------
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
            buttons.append(
                [{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}]
            )
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    @staticmethod
    def _display_user(record: dict[str, Any], user_id: str) -> str:
        full_name = " ".join(
            value
            for value in (
                str(record.get("first_name") or "").strip(),
                str(record.get("last_name") or "").strip(),
            )
            if value
        )
        username = str(record.get("username") or "").strip().lstrip("@")
        return full_name or (f"@{username}" if username else user_id)

    def show_access(self, page: int = 0) -> None:
        if not self.is_owner():
            self.send(
                "Управление доступом доступно только владельцу.",
                reply_markup=self.with_nav(),
            )
            return
        access = self.load_access(force=True)
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        ordered = sorted(
            (
                (str(user_id), record)
                for user_id, record in users.items()
                if isinstance(record, dict)
            ),
            key=lambda item: self._display_user(item[1], item[0]).casefold(),
        )
        pages = max(1, math.ceil(len(ordered) / ACCESS_PAGE_SIZE))
        page = max(0, min(int(page), pages - 1))
        part = ordered[page * ACCESS_PAGE_SIZE : (page + 1) * ACCESS_PAGE_SIZE]
        admins = {str(value) for value in access.get("admins", [])}
        lines = [
            "👥 <b>Доступ и администраторы</b>",
            "",
            f"Пользователей: <b>{len(ordered)}</b>",
            f"Администраторов: <b>{len(admins)}</b>",
        ]
        if pages > 1:
            lines.append(f"Страница: <b>{page + 1} из {pages}</b>")
        lines.append("")
        buttons: list[list[dict[str, str]]] = []
        for user_id, record in part:
            role = self.role_for(user_id)
            name = self._display_user(record, user_id)
            lines.append(f"• {html.escape(name)} — {self.role_name(role)}")
            buttons.append(
                [
                    {
                        "text": f"{name[:24]} · {self.role_name(role)}",
                        "callback_data": f"page:user:{user_id}",
                    }
                ]
            )
        if not part:
            lines.append("Пользователи ещё не запускали бота.")

        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append(
                {"text": "◀️ Назад", "callback_data": f"page:access:{page - 1}"}
            )
        if page < pages - 1:
            pager.append(
                {"text": "Вперёд ▶️", "callback_data": f"page:access:{page + 1}"}
            )
        if pager:
            buttons.append(pager)
        buttons.extend(
            [
                [
                    {
                        "text": "🔄 Обновить",
                        "callback_data": f"page:access:{page}",
                    }
                ],
                [
                    {
                        "text": "➕ Добавить администратора по ID",
                        "callback_data": "access:add_admin",
                    }
                ],
            ]
        )
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def show_recipients(self, page: int = 0) -> None:
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        access = self.load_access(force=True)
        recipients = {str(value) for value in access.get("notification_recipients", [])}
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        ordered = sorted(
            (
                (str(user_id), record)
                for user_id, record in users.items()
                if isinstance(record, dict)
            ),
            key=lambda item: self._display_user(item[1], item[0]).casefold(),
        )
        pages = max(1, math.ceil(len(ordered) / ACCESS_PAGE_SIZE))
        page = max(0, min(int(page), pages - 1))
        part = ordered[page * ACCESS_PAGE_SIZE : (page + 1) * ACCESS_PAGE_SIZE]
        lines = ["🔔 <b>Получатели новых колёс</b>", ""]
        if pages > 1:
            lines.append(f"Страница: <b>{page + 1} из {pages}</b>\n")
        buttons: list[list[dict[str, str]]] = []
        for user_id, record in part:
            chat_id = str(record.get("chat_id") or user_id)
            enabled = bool(record.get("notifications_enabled", chat_id in recipients))
            name = self._display_user(record, user_id)
            lines.append(f"{self.bool_mark(enabled)} {html.escape(name)}")
            buttons.append(
                [
                    {
                        "text": f"{self.bool_mark(enabled)} {name[:26]}",
                        "callback_data": f"recipient:{user_id}",
                    }
                ]
            )
        if not part:
            lines.append("Пользователи ещё не запускали бота.")
        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append(
                {"text": "◀️ Назад", "callback_data": f"page:recipients:{page - 1}"}
            )
        if page < pages - 1:
            pager.append(
                {"text": "Вперёд ▶️", "callback_data": f"page:recipients:{page + 1}"}
            )
        if pager:
            buttons.append(pager)
        buttons.append(
            [
                {
                    "text": "🔄 Обновить",
                    "callback_data": f"page:recipients:{page}",
                }
            ]
        )
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    # ---------- Routing and stale-button compatibility ----------
    def render_page(self, page: str) -> None:
        normalized = self._normalize_page(page)
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

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        for action in ("part", "inactive", "finished", "time"):
            prefix = f"wheel:{action}:"
            if not data.startswith(prefix):
                continue
            token = data[len(prefix) :]
            if not token.startswith("~"):
                break
            resolved = self._resolve_wheel_token(token)
            if not resolved:
                self._prepare_callback_user(query)
                self.answer(str(query.get("id") or ""), "Колесо уже не активно")
                return
            query = dict(query)
            query["data"] = prefix + resolved
            data = str(query["data"])
            break
        if data.startswith(("wheel:part:", "bb:p:")):
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            try:
                if data.startswith("wheel:part:"):
                    key = data.split(":", 2)[2].casefold()
                    admin_action = ("participate_wheel", key)
                else:
                    token = data.split(":", 2)[2]
                    context = self.snapshot(force=True).state.get(
                        "button_contexts", {}
                    ).get(token)
                    if not isinstance(context, dict):
                        raise ValueError("Контекст кнопки устарел")
                    key = str(
                        context.get("wheel_key")
                        or context.get("identifier")
                        or ""
                    ).casefold()
                    admin_action = ("participate_token", token)
                if not key:
                    raise ValueError("Колесо не определено")
                if self.is_admin():
                    self.dispatch_admin_action(*admin_action)
                self.mark_personal_participation(key)
            except Exception as exc:
                print(f"ERROR isolated participation: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось сохранить участие")
                return
            self.answer(
                query_id,
                "Ваше участие отмечено; колесо подтверждено"
                if self.is_admin()
                else "Ваше участие отмечено",
            )
            self.show_active()
            return
        super().handle_callback(query)


def self_test() -> None:
    telegram_ui.self_test()
    panel = TelegramPanelRuntimeV38()
    user_main = {
        button["callback_data"]
        for row in panel.compact_menu_rows(False)
        for button in row
    }
    admin_main = {
        button["callback_data"]
        for row in panel.compact_menu_rows(True)
        for button in row
    }
    assert "page:status" in user_main and "page:control" not in user_main
    assert "page:control" in admin_main and "page:status" not in admin_main
    assert panel._normalize_page("reports") == "analytics:1"
    assert panel._normalize_page("stats:30") == "analytics:30"

    long_key = "https://example.invalid/" + "очень-длинный-ключ-" * 8
    callback = panel._wheel_callback("inactive", long_key)
    assert len(callback.encode("utf-8")) <= telegram_ui.TELEGRAM_CALLBACK_LIMIT

    captured: list[tuple[str, dict[str, Any]]] = []
    panel.current_user_id = "1"
    panel.current_role = "owner"
    panel.navigation = {"1": ["menu", "active:0"]}
    panel.is_admin = lambda: True  # type: ignore[method-assign]
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
    panel._sources_for_item = lambda snap, key, item: ["mechanogun"]  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
        {
            "_key": f"wheel-{index}",
            "identifier": f"wheel-{index}",
            "source": "mechanogun",
            "url": f"https://betboom.ru/freestream/wheel-{index}",
        }
        for index in range(14)
    ]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.show_active(1)
    text, kwargs = captured[-1]
    assert "Страница: <b>2 из 3</b>" in text
    assert "wheel-6" in text and "wheel-12" not in text
    assert not telegram_ui.markup_issues(kwargs["reply_markup"])
    print("admin panel v38 chapter 4 UI self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV38().run()


if __name__ == "__main__":
    raise SystemExit(main())
