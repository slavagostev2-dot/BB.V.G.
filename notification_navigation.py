from __future__ import annotations

import copy
import html
from typing import Any, Callable


MAIN_MENU_CALLBACK = "page:menu"
ACTIVE_LIST_CALLBACK = "bb:l:active"
ACTIVE_LIST_CALLBACKS = {ACTIVE_LIST_CALLBACK, "page:active"}
WHEEL_MARKERS = ("колес", "wheel")


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

    Every notification gets a route back to the bot menu. Notifications related
    to a wheel also get a stable route to the current active-wheel list.
    Existing wheel, participation and URL buttons are preserved.
    """

    lowered = html.unescape(str(text or "")).casefold()
    wheel_notification = any(marker in lowered for marker in WHEEL_MARKERS)
    source = copy.deepcopy(reply_markup) if isinstance(reply_markup, dict) else {}
    raw_rows = source.get("inline_keyboard")
    rows: list[list[dict[str, Any]]] = []

    if isinstance(raw_rows, list):
        for raw_row in raw_rows:
            if not isinstance(raw_row, list):
                continue
            row = [dict(button) for button in raw_row if isinstance(button, dict)]
            if row:
                rows.append(row)
    elif url:
        rows.append([{"text": "🎡 Открыть колесо", "url": url}])

    if wheel_notification and not any(
        _button_is_active_list(button) for row in rows for button in row
    ):
        rows.append(
            [{"text": "🔥 Активные колёса", "callback_data": ACTIVE_LIST_CALLBACK}]
        )

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
            [{"text": "✅ Участвую", "callback_data": "bb:p:token"}],
        ]
    }
    fresh = notification_markup("🎡 Новое колесо BetBoom", None, original)
    fresh_text = str(fresh)
    assert ACTIVE_LIST_CALLBACK in fresh_text
    assert MAIN_MENU_CALLBACK in fresh_text
    assert "https://example.test/wheel" in fresh_text

    reminder = notification_markup("⏰ Напоминание о колесе BetBoom", None, original)
    reminder_text = str(reminder)
    assert ACTIVE_LIST_CALLBACK in reminder_text
    assert MAIN_MENU_CALLBACK in reminder_text

    plain = notification_markup("✅ Проверка завершена", None, None)
    assert plain == {
        "inline_keyboard": [[{"text": "🏠 Главное меню", "callback_data": MAIN_MENU_CALLBACK}]]
    }

    duplicate = notification_markup(
        "Служебное уведомление о колесе",
        None,
        {
            "inline_keyboard": [
                [{"text": "🔥 Активные колёса", "callback_data": ACTIVE_LIST_CALLBACK}],
                [{"text": "🏠 Главное меню", "callback_data": MAIN_MENU_CALLBACK}],
            ]
        },
    )
    duplicate_text = str(duplicate)
    assert duplicate_text.count(ACTIVE_LIST_CALLBACK) == 1
    assert duplicate_text.count(MAIN_MENU_CALLBACK) == 1
    print("notification navigation self-test passed")


if __name__ == "__main__":
    self_test()
