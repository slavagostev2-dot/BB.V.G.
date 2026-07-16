from __future__ import annotations

import argparse
import html
import inspect
from typing import Any

import bot_private_state
from bbvg.bot.source_requests import default_source_requests
from bbvg.bot.users import UserManagementRuntime
from bbvg.bot.storage import PrivateStateRuntime, self_test as storage_self_test

CONFIRMED_POINTS = 40


class TelegramPanelRuntimeV25(PrivateStateRuntime):
    """Compatibility UI over the consolidated encrypted-state subsystem.

    Storage, conflict resolution and role persistence now live in
    ``bbvg.bot.storage``. The remaining methods preserve the historical v25
    page and callback contract until the later interface layers are consolidated.
    """

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
                    {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                    {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                ],
            ]
        return [
            [
                {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
            ],
            [{"text": "📡 Источники", "callback_data": "page:sources"}],
        ]

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
            "Основной и ночной режимы входят в один реестр: каждый источник отображается один раз.",
        ]
        if problems:
            lines.extend(["", "<b>Требуют внимания</b>"])
            for row in problems[:12]:
                username = str(row.get("username") or "неизвестно")
                reason = str(row.get("reason") or "нет данных")[:180]
                lines.append(f"• @{html.escape(username)} — {html.escape(reason)}")
            if len(problems) > 12:
                lines.append(f"• ещё {len(problems) - 12}")
        rows = [
            [
                {"text": "🔄 Обновить реестр", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
            ],
            [
                {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
            ],
        ]
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_ranking(self) -> None:
        snap = self.snapshot()
        source_rows = snap.stats.get("sources", {}) if isinstance(snap.stats, dict) else {}
        ranked: list[tuple[str, int, int, int]] = []
        if isinstance(source_rows, dict):
            for source, row in source_rows.items():
                if not isinstance(row, dict):
                    continue
                score = max(0, int(row.get("quality_score", 0) or 0))
                confirmed = int(row.get("admin_confirmed_wheels", 0) or 0)
                inactive = int(row.get("admin_rejected_wheels", 0) or 0)
                if score or confirmed or inactive:
                    ranked.append((str(source), score, confirmed, inactive))
        ranked.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            f"Подтверждение администратором: <b>+{CONFIRMED_POINTS}</b> очков.",
            "Отметка «Неактивное» удаляет колесо, но рейтинг источника не уменьшает.",
            "Личная кнопка пользователя «Участвую» на рейтинг не влияет.",
            "Повторное решение по тому же колесу не начисляет очки повторно.",
            "",
        ]
        for index, (source, score, confirmed, inactive) in enumerate(ranked[:25], 1):
            lines.append(
                f"<b>{index}. @{html.escape(source)}</b> — {score} оч. "
                f"(подтверждено: {confirmed}, удалено как неактивное: {inactive})"
            )
        if not ranked:
            lines.append("Рейтинг начнёт формироваться после первого подтверждения администратора.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить рейтинг", "callback_data": "page:ranking"}]]
            ),
        )

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot()
        participating = self._joined_wheel_keys(snap)
        if not items:
            self.send(
                "🔥 <b>BB V.G.: активных колёс сейчас нет.</b>",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить список", "callback_data": "refresh:active"}]]
                ),
            )
            return

        admin = self.is_admin()
        lines = [f"🔥 <b>BB V.G.: активные колёса — {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier).casefold()
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            joined = identifier.casefold() in participating or key in participating
            time_text = self.remaining(deadline) if deadline else "🔴 Время прокрутки неизвестно"
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                    f"⏳ {html.escape(time_text)}",
                    f"📡 @{html.escape(source)}",
                    "✅ Активность подтверждена администратором"
                    if admin and joined
                    else ("✅ Участие отмечено" if joined else "❌ Участие не отмечено"),
                    "",
                ]
            )
            url = str(item.get("url") or "")
            if url:
                buttons.append([{"text": f"🎡 Открыть {index}", "url": url}])
            actions: list[dict[str, str]] = []
            if not joined:
                actions.append(
                    {
                        "text": "✅ Участвую (+40)" if admin else "✅ Участвую",
                        "callback_data": f"wheel:part:{key}",
                    }
                )
            actions.append(
                {
                    "text": "🚫 Неактивное" if admin else "🚫 Скрыть у меня",
                    "callback_data": f"wheel:inactive:{key}",
                }
            )
            buttons.append(actions)
            if admin and not deadline:
                buttons.append(
                    [{"text": "⏱ Указать время", "callback_data": f"wheel:time:{key}"}]
                )
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "refresh:active"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def handle_callback(self, query: dict[str, Any]) -> None:
        message = query.get("message") if isinstance(query, dict) else None
        message = message if isinstance(message, dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = query.get("from") if isinstance(query, dict) else None
        sender = sender if isinstance(sender, dict) else {}
        registration_message = {"chat": chat, "from": sender}
        if self.private_chat(registration_message) and sender.get("id"):
            self.set_context(chat.get("id"), sender.get("id"))
            self.register_user(registration_message)
            self.set_context(chat.get("id"), sender.get("id"))
        super().handle_callback(query)

    def render_page(self, page: str) -> None:
        if page == "app":
            self.send(
                "📦 <b>Приложение временно отключено</b>\n\n"
                "Рабочий контур BB V.G. сейчас находится только в Telegram-боте.",
                reply_markup=self.with_nav(),
            )
            return
        if page in {"discovery", "intelligence"}:
            UserManagementRuntime.render_page(self, page)
            return
        if page in {"status", "reports", "pending"}:
            self.show_menu(clear_stack=True)
            return
        if page == "ranking":
            self.show_ranking()
            return
        if page == "sources":
            self.show_sources()
            return
        super().render_page(page)


def self_test() -> None:
    storage_self_test()
    admin_callbacks = {
        button.get("callback_data")
        for row in TelegramPanelRuntimeV25.compact_menu_rows(True)
        for button in row
    }
    assert "page:app" not in admin_callbacks
    assert "page:ranking" not in admin_callbacks
    assert "page:discovery" in admin_callbacks
    assert "page:intelligence" in admin_callbacks
    source = inspect.getsource(TelegramPanelRuntimeV25.handle_callback)
    assert "register_user" in source
    assert "private_chat" in source

    panel = TelegramPanelRuntimeV25()
    panel._bot_bundle = bot_private_state.default_bundle(
        panel._bootstrap_access(), default_source_requests()
    )
    panel._bundle_baseline = None
    panel._save_bot_bundle = lambda message: True  # type: ignore[method-assign]
    panel.send = lambda *args, **kwargs: {"ok": True}  # type: ignore[method-assign]
    panel.answer = lambda *args, **kwargs: None  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "callback-test",
            "from": {"id": 2, "username": "button_user", "first_name": "Button"},
            "message": {"message_id": 1, "chat": {"id": 2, "type": "private"}},
            "data": "nav:home",
        }
    )
    assert "2" in panel.access.get("users", {})
    assert panel.role_for("2") == "user"
    print("admin_panel_runtime_v25 storage compatibility self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV25().run()


if __name__ == "__main__":
    raise SystemExit(main())
