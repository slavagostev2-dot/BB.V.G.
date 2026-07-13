from __future__ import annotations

import argparse
import html
from typing import Any

from admin_panel_runtime_v8 import TelegramPanelRuntimeV8
from admin_panel_runtime_v7 import MINI_APP_URL
from admin_panel_runtime_v6 import BTN_INTELLIGENCE, BTN_NIGHTLY

BTN_APP = "📱 Приложение"

ADMIN_KEYBOARD_V9 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": BTN_NIGHTLY}],
        [{"text": BTN_INTELLIGENCE}, {"text": "⚙️ Настройки"}],
        [{"text": BTN_APP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Панель BetBoom Monitor",
}

USER_KEYBOARD_V9 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": BTN_APP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "BetBoom Monitor",
}


class TelegramPanelRuntimeV9(TelegramPanelRuntimeV8):
    """Panel v9: no duplicated control menu, reliable Mini App entry."""

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        keyboard = ADMIN_KEYBOARD_V9 if role in {"owner", "admin"} else USER_KEYBOARD_V9
        title = "панель управления" if role in {"owner", "admin"} else "информационная панель"
        self.send(
            f"🎡 <b>BetBoom Monitor — {title}</b>\n\n"
            f"Ваш доступ: <b>{self.role_name(role)}</b>\n"
            "Каждое действие находится только в своём разделе. Отдельная вкладка управления удалена.",
            reply_markup=keyboard,
        )

    def show_app_entry(self) -> None:
        self.send(
            "📱 <b>Приложение BetBoom Monitor</b>\n\n"
            "Откройте его внутри Telegram. Если клиент Telegram не поддерживает Web App-кнопку, используйте резервную ссылку в браузере.",
            reply_markup=self.with_nav([
                [{"text": "📱 Открыть внутри Telegram", "web_app": {"url": MINI_APP_URL}}],
                [{"text": "🌐 Открыть в браузере", "url": MINI_APP_URL}],
            ]),
        )

    def show_settings(self) -> None:
        super().show_settings()
        # The settings screen itself is sent by the parent. A separate status entry
        # remains available through callbacks and commands, but is removed from the
        # persistent keyboard to avoid duplication.

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
                reply_markup=self.with_nav([[{"text": "🔄 Обновить список", "callback_data": "refresh:active"}]]),
            )
            return

        lines = [f"🔥 <b>Действующие колёса: {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier)
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            live_state = str(item.get("_live_state") or "checking")
            status_text = {
                "active": "🟢 участие открыто",
                "scheduled": "🟡 прокрутка впереди",
                "checking": "🟠 проверяется",
            }.get(live_state, "🟠 проверяется")
            participates = identifier.casefold() in participating or key.casefold() in participating
            lines.extend([
                f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                status_text,
                f"⏳ {html.escape(self.remaining(deadline) if deadline else 'время не определено')}",
                f"📡 @{html.escape(source)}",
                f"🙋 {'✅ участие отмечено' if participates else '❌ участие не отмечено'}",
                "",
            ])
            row: list[dict[str, str]] = []
            url = str(item.get("url") or "")
            if url:
                row.append({"text": "🎡 Открыть колесо", "url": url})
            if not participates:
                row.append({"text": "✅ Я участвую", "callback_data": f"wheel:part:{key}"})
            if row:
                buttons.append(row)
            if self.is_admin():
                buttons.append([{"text": "🗑 Убрать из списка", "callback_data": f"wheel:removeask:{key}"}])
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "refresh:active"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        self.set_context(chat.get("id"), sender.get("id"))
        text = str(message.get("text") or "").strip()
        command = text.split("@", 1)[0].split(maxsplit=1)[0].casefold() if text else ""
        if text == BTN_APP:
            self.navigation[str(self.current_user_id)] = ["menu"]
            self.open_page("app")
            return
        # Old buttons are intentionally redirected instead of remaining broken.
        if text == "✅ Проверка работы":
            self.navigation[str(self.current_user_id)] = ["menu"]
            self.open_page("status")
            return
        if text == "🛠 Управление":
            self.show_menu(clear_stack=True)
            return
        super().handle_message(message)

    def render_page(self, page: str) -> None:
        if page == "app":
            self.show_app_entry()
            return
        super().render_page(page)


def self_test() -> None:
    panel = TelegramPanelRuntimeV9()
    assert "🛠 Управление" not in str(ADMIN_KEYBOARD_V9)
    assert "✅ Проверка работы" not in str(ADMIN_KEYBOARD_V9)
    assert BTN_NIGHTLY in str(ADMIN_KEYBOARD_V9)
    assert BTN_INTELLIGENCE in str(ADMIN_KEYBOARD_V9)
    assert BTN_APP in str(USER_KEYBOARD_V9)
    assert "Перепроверить" not in TelegramPanelRuntimeV9.show_active.__code__.co_consts
    print("admin_panel_runtime_v9 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV9().run()


if __name__ == "__main__":
    raise SystemExit(main())
