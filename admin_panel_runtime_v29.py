from __future__ import annotations

import argparse
import html
from typing import Any

from admin_panel_runtime_v28 import TelegramPanelRuntimeV28


class TelegramPanelRuntimeV29(TelegramPanelRuntimeV28):
    """Role-aware grouped menu without duplicated sections."""

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
            rows.append([{"text": "✅ Состояние системы", "callback_data": "page:status"}])
        return rows

    @staticmethod
    def source_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = [
            [
                {"text": "🔄 Обновить реестр", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
            ]
        ]
        if admin:
            rows.extend(
                [
                    [
                        {"text": "⚡ Основные источники", "callback_data": "source_list:primary:0"},
                        {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
                    ],
                    [
                        {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                        {"text": "➕ Добавить источник", "callback_data": "source:add"},
                    ],
                ]
            )
        else:
            rows.append(
                [
                    {"text": "📋 Основные источники", "callback_data": "source_list:primary:0"},
                    {"text": "➕ Предложить источник", "callback_data": "source:request_help"},
                ]
            )
        return rows

    @staticmethod
    def control_menu_rows() -> list[list[dict[str, Any]]]:
        return [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "📨 Отправить ежедневную сводку", "callback_data": "control:daily"}],
            [{"text": "✅ Состояние системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]

    def show_analytics(self) -> None:
        self.send(
            "📊 <b>Аналитика</b>\n\n"
            "Статистика показывает текущие показатели по периодам. "
            "Отчёты содержат сводки, неактивные источники и ошибки проверки.",
            reply_markup=self.with_nav(
                [
                    [
                        {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                        {"text": "📅 Отчёты", "callback_data": "page:reports"},
                    ]
                ]
            ),
        )

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot()
        totals = self.period_totals(snap.stats, days)
        title = "сегодня" if days == 1 else f"за {days} дней"
        text = (
            f"📊 <b>BB V.G.: статистика {title}</b>\n\n"
            f"Проверок источников: {totals.get('checks', 0)}\n"
            f"Просмотрено сообщений: {totals.get('messages_scanned', 0)}\n"
            f"Найдено постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Отправлено уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Колёс с подтверждённым временем: {totals.get('activation_sent', 0)}\n"
            f"Повторы подавлены: {totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок проверки: {totals.get('errors', 0)}\n\n"
            f"Сейчас отображается колёс: {len(self._collect_current_wheels())}"
        )
        rows = [
            [
                {"text": "Сегодня", "callback_data": "page:stats:1"},
                {"text": "7 дней", "callback_data": "page:stats:7"},
                {"text": "30 дней", "callback_data": "page:stats:30"},
            ]
        ]
        self.send(text, reply_markup=self.with_nav(rows))

    def show_reports(self) -> None:
        rows = [
            [
                {"text": "Сегодня", "callback_data": "page:report:1"},
                {"text": "7 дней", "callback_data": "page:report:7"},
                {"text": "30 дней", "callback_data": "page:report:30"},
            ],
            [{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}],
            [{"text": "⚠️ Ошибки источников", "callback_data": "page:report:errors"}],
        ]
        self.send(
            "📅 <b>Отчёты</b>\n\nВыберите период или специализированный отчёт.",
            reply_markup=self.with_nav(rows),
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
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"Всего в едином реестре: <b>{int(summary.get('total', 0) or 0)}</b>",
            f"Проверено: <b>{int(summary.get('checked', 0) or 0)}</b>",
            f"Доступно: <b>{int(summary.get('available', 0) or 0)}</b>",
            f"Недоступно: <b>{int(summary.get('unavailable', 0) or 0)}</b>",
            f"Ожидает первой проверки: <b>{int(summary.get('pending', 0) or 0)}</b>",
            "",
            "Каждый источник отображается в реестре один раз.",
        ]
        if problems:
            lines.extend(["", "<b>Требуют внимания</b>"])
            for row in problems[:12]:
                username = str(row.get("username") or "неизвестно")
                reason = str(row.get("reason") or "нет данных")[:180]
                lines.append(f"• @{html.escape(username)} — {html.escape(reason)}")
            if len(problems) > 12:
                lines.append(f"• ещё {len(problems) - 12}")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(self.source_menu_rows(self.is_admin())),
        )

    def show_control(self) -> None:
        if not self.is_admin():
            self.send("Управление доступно только администраторам.", reply_markup=self.with_nav())
            return
        self.send(
            "🛠 <b>Управление</b>\n\n"
            "Здесь находятся ручные системные действия и диагностика. "
            "Разведка и ночное наблюдение находятся только в разделе «Источники».",
            reply_markup=self.with_nav(self.control_menu_rows()),
        )

    def show_source_request_help(self) -> None:
        self.send(
            "➕ <b>Предложить источник</b>\n\n"
            "Отправьте боту команду в формате:\n"
            "<code>/source @channel_name</code>\n\n"
            "Бот проверит публичность канала и передаст заявку администратору.",
            reply_markup=self.with_nav(),
        )

    def render_page(self, page: str) -> None:
        if page == "analytics":
            self.show_analytics()
            return
        if page == "reports":
            self.show_reports()
            return
        if page == "status":
            self.show_status()
            return
        if page == "sources":
            self.show_sources()
            return
        if page == "control":
            self.show_control()
            return
        if page in {"discovery", "intelligence"} and not self.is_admin():
            self.send("Этот раздел доступен только администраторам.", reply_markup=self.with_nav())
            return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data == "source:request_help":
            self._prepare_callback_user(query)
            self.answer(str(query.get("id") or ""), "Как предложить источник")
            self.show_source_request_help()
            return
        super().handle_callback(query)


def _callbacks(rows: list[list[dict[str, Any]]]) -> list[str]:
    return [str(button.get("callback_data") or "") for row in rows for button in row]


def self_test() -> None:
    admin_main = _callbacks(TelegramPanelRuntimeV29.compact_menu_rows(True))
    user_main = _callbacks(TelegramPanelRuntimeV29.compact_menu_rows(False))
    assert len(admin_main) == len(set(admin_main))
    assert len(user_main) == len(set(user_main))
    assert set(admin_main) == {
        "page:active",
        "page:analytics",
        "page:sources",
        "page:settings",
        "page:control",
    }
    assert set(user_main) == {
        "page:active",
        "page:analytics",
        "page:sources",
        "page:settings",
        "page:status",
    }

    admin_sources = _callbacks(TelegramPanelRuntimeV29.source_menu_rows(True))
    user_sources = _callbacks(TelegramPanelRuntimeV29.source_menu_rows(False))
    assert len(admin_sources) == len(set(admin_sources))
    assert len(user_sources) == len(set(user_sources))
    assert "page:discovery" in admin_sources
    assert "page:intelligence" in admin_sources
    assert "source:add" in admin_sources
    assert "page:discovery" not in user_sources
    assert "page:intelligence" not in user_sources
    assert "source:add" not in user_sources
    assert "source:request_help" in user_sources

    control = _callbacks(TelegramPanelRuntimeV29.control_menu_rows())
    assert len(control) == len(set(control))
    assert "page:intelligence" not in control
    assert "page:discovery" not in control
    assert set(control) == {
        "control:monitor",
        "control:daily",
        "page:status",
        "page:diagnostic",
    }

    panel = TelegramPanelRuntimeV29()
    assert panel._json_text('{"ok": true}', {}) == {"ok": True}
    assert panel._serialize_json({"ok": True}).strip() == '{\n  "ok": true\n}'
    print("admin_panel_runtime_v29 grouped role menu self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV29().run()


if __name__ == "__main__":
    raise SystemExit(main())
