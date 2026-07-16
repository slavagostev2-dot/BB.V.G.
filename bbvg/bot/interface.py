from __future__ import annotations

import html
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from admin_panel_runtime_v14 import TelegramPanelRuntimeV14

UTC = timezone.utc


class PanelInterfaceRuntime(TelegramPanelRuntimeV14):
    """Final menu, statistics and pending-wheel interface formerly in v15-v16."""

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        if admin:
            return [
                [
                    {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                    {"text": "🔥 Активные колёса", "callback_data": "page:active"},
                ],
                [
                    {"text": "📡 Источники", "callback_data": "page:sources"},
                    {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
                ],
                [
                    {
                        "text": "🛰️ Разведка источников",
                        "callback_data": "page:intelligence",
                    },
                    {"text": "🏆 Рейтинг каналов", "callback_data": "page:ranking"},
                ],
                [
                    {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                    {"text": "✅ Состояние системы", "callback_data": "page:status"},
                ],
                [{"text": "📱 Приложение", "callback_data": "page:app"}],
            ]
        return [
            [
                {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
            ],
            [
                {"text": "📡 Источники", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг каналов", "callback_data": "page:ranking"},
            ],
            [{"text": "📱 Приложение", "callback_data": "page:app"}],
        ]

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
            ],
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


def self_test() -> None:
    admin_rows = PanelInterfaceRuntime.compact_menu_rows(True)
    callbacks = [button.get("callback_data") for row in admin_rows for button in row]
    expected = {
        "page:stats:1",
        "page:active",
        "page:sources",
        "page:discovery",
        "page:intelligence",
        "page:ranking",
        "page:settings",
        "page:status",
        "page:app",
    }
    assert set(callbacks) == expected
    assert "page:more" not in callbacks
    assert "page:reports" not in callbacks
    assert all(len(row) <= 2 for row in admin_rows)

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
    print("BB V.G. panel interface self-test passed")


if __name__ == "__main__":
    self_test()
