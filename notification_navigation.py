from __future__ import annotations

import copy
import html
from typing import Any, Callable


MAIN_MENU_CALLBACK = "page:menu"
ACTIVE_LIST_CALLBACKS = {"bb:l:active", "page:active"}
NEW_WHEEL_MARKER = "новое колесо betboom"


def _button_is_main_menu(button: dict[str, Any]) -> bool:
    callback = str(button.get("callback_data") or "")
    text = html.unescape(str(button.get("text") or "")).casefold()
    return callback == MAIN_MENU_CALLBACK or "главное меню" in text


def _button_is_active_list(button: dict[str, Any]) -> bool:
    callback = str(button.get("callback_data") or "")
    text = html.unescape(str(button.get("text") or "")).casefold()
    return callback in ACTIVE_LIST_CALLBACKS or "активные колёса" in text or "активные колеса" in text


def notification_markup(
    text: str,
    url: str | None,
    reply_markup: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return one consistent inline keyboard for every automated notification.

    Every notification gets a route back to the bot menu. A fresh-wheel alert is
    intentionally kept compact and does not contain the redundant active-list
    button. Other wheel reminders may keep that button.
    """

    lowered = html.unescape(str(text or "")).casefold()
    new_wheel = NEW_WHEEL_MARKER in lowered
    source = copy.deepcopy(reply_markup) if isinstance(reply_markup, dict) else {}
    raw_rows = source.get("inline_keyboard")
    rows: list[list[dict[str, Any]]] = []

    if isinstance(raw_rows, list):
        for raw_row in raw_rows:
            if not isinstance(raw_row, list):
                continue
            row: list[dict[str, Any]] = []
            for raw_button in raw_row:
                if not isinstance(raw_button, dict):
                    continue
                button = dict(raw_button)
                if new_wheel and _button_is_active_list(button):
                    continue
                row.append(button)
            if row:
                rows.append(row)
    elif url:
        rows.append([{"text": "🎡 Открыть колесо", "url": url}])

    if not any(_button_is_main_menu(button) for row in rows for button in row):
        rows.append([{"text": "🏠 Главное меню", "callback_data": MAIN_MENU_CALLBACK}])

    return {"inline_keyboard": rows}


def install(monitor_module: Any) -> None:
    """Install navigation decoration after the notification router."""

    if getattr(monitor_module, "_bbvg_notification_navigation_installed", False):
        return
    original: Callable = monitor_module.send_message

    def send_with_navigation(
        text: str,
        url: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict:
        markup = notification_markup(text, url, reply_markup)
        return original(text, url=None, reply_markup=markup)

    monitor_module.send_message = send_with_navigation
    monitor_module._bbvg_notification_navigation_installed = True


def self_test() -> None:
    original = {
        "inline_keyboard": [
            [{"text": "🎡 Открыть колесо", "url": "https://example.test/wheel"}],
            [
                {"text": "✅ Участвую", "callback_data": "bb:p:token"},
                {"text": "📋 Активные колёса", "callback_data": "bb:l:active"},
            ],
        ]
    }
    fresh = notification_markup("🎡 Новое колесо BetBoom", None, original)
    fresh_text = str(fresh)
    assert "bb:l:active" not in fresh_text
    assert MAIN_MENU_CALLBACK in fresh_text

    reminder = notification_markup("⏰ Напоминание о колесе BetBoom", None, original)
    reminder_text = str(reminder)
    assert "bb:l:active" in reminder_text
    assert MAIN_MENU_CALLBACK in reminder_text

    plain = notification_markup("✅ Проверка завершена", None, None)
    assert plain == {
        "inline_keyboard": [[{"text": "🏠 Главное меню", "callback_data": MAIN_MENU_CALLBACK}]]
    }

    duplicate = notification_markup(
        "Служебное уведомление",
        None,
        {"inline_keyboard": [[{"text": "🏠 Главное меню", "callback_data": MAIN_MENU_CALLBACK}]]},
    )
    assert str(duplicate).count(MAIN_MENU_CALLBACK) == 1
    print("notification navigation self-test passed")


if __name__ == "__main__":
    self_test()
