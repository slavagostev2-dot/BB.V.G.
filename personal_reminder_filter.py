from __future__ import annotations

import html
from typing import Any, Callable


REMINDER_MARKERS = (
    "напоминание о колесе betboom",
    "последний шанс войти в колесо betboom",
    "вы ещё не отметили участие",
    "вы еще не отметили участие",
)


def participating_for_chat(config: dict[str, Any], chat_id: str, wheel_key: str) -> bool:
    if not wheel_key:
        return False
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    record: dict[str, Any] = {}
    for user_id, raw in users.items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("chat_id") or user_id) == str(chat_id):
            record = raw
            break
    raw = record.get("participating_wheels")
    if isinstance(raw, list):
        return wheel_key.casefold() in {str(value).casefold() for value in raw}
    if isinstance(raw, dict):
        return wheel_key.casefold() in {str(value).casefold() for value in raw}
    return False


def install(monitor_module: Any, router_module: Any) -> None:
    """Filter only reminder deliveries, keeping initial wheel alerts unchanged."""

    if getattr(monitor_module, "_bbvg_personal_reminder_filter_installed", False):
        return
    original_api: Callable = monitor_module.telegram_api

    def telegram_api_filtered(method: str, payload: dict) -> dict:
        if method == "sendMessage" and isinstance(payload, dict):
            text = html.unescape(str(payload.get("text") or "")).casefold()
            if any(marker in text for marker in REMINDER_MARKERS):
                config, _ = router_module.load_config()
                key = router_module.wheel_key_from_message(
                    str(payload.get("text") or ""),
                    None,
                    payload.get("reply_markup") if isinstance(payload.get("reply_markup"), dict) else None,
                )
                chat_id = str(payload.get("chat_id") or "")
                if participating_for_chat(config, chat_id, key):
                    return {
                        "ok": True,
                        "result": {
                            "suppressed": True,
                            "reason": "participation_already_marked",
                            "chat_id": chat_id,
                            "wheel_key": key,
                        },
                    }
        return original_api(method, payload)

    monitor_module.telegram_api = telegram_api_filtered
    monitor_module._bbvg_personal_reminder_filter_installed = True


def self_test() -> None:
    config = {
        "users": {
            "1": {
                "chat_id": "10",
                "participating_wheels": {"wheel-a": {"joined_at": "now"}},
            },
            "2": {"chat_id": "20", "participating_wheels": {}},
        }
    }
    assert participating_for_chat(config, "10", "wheel-a")
    assert not participating_for_chat(config, "20", "wheel-a")
    assert not participating_for_chat(config, "10", "wheel-b")
    print("personal reminder filter self-test passed")


if __name__ == "__main__":
    self_test()
