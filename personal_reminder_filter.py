from __future__ import annotations

import html
from typing import Any, Callable

import personal_wheel_voting


REMINDER_MARKERS = (
    "напоминание о колесе betboom",
    "последний шанс войти в колесо betboom",
    "вы ещё не отметили участие",
    "вы еще не отметили участие",
)


def set_global_participating(wheel_key: str, participating: bool) -> None:
    """Compatibility no-op: global participation no longer suppresses reminders."""


def _user_record(config: dict[str, Any], chat_id: str) -> dict[str, Any]:
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    for user_id, raw in users.items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("chat_id") or user_id) == str(chat_id):
            return raw
    return {}


def participating_for_chat(
    config: dict[str, Any],
    chat_id: str,
    wheel_key: str,
    entry: dict[str, Any] | None = None,
) -> bool:
    normalized = str(wheel_key or "").casefold()
    if not normalized:
        return False
    record = _user_record(config, chat_id)
    raw = record.get("participating_wheels")
    if isinstance(raw, list):
        joined = {str(value).casefold() for value in raw}
        return normalized in joined and not bool((entry or {}).get("action_id") or (entry or {}).get("event_id"))
    if not isinstance(raw, dict):
        return False
    event_key = personal_wheel_voting.wheel_event_key(normalized, entry)
    if event_key in {str(value).casefold() for value in raw}:
        return True
    if event_key != normalized:
        return False
    return normalized in {str(value).casefold() for value in raw}


def install(monitor_module: Any, router_module: Any) -> None:
    """Filter final reminders by each recipient's exact wheel-event participation."""

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
                    payload.get("reply_markup")
                    if isinstance(payload.get("reply_markup"), dict)
                    else None,
                )
                state = monitor_module.load_state()
                active = state.get("active_wheels") if isinstance(state, dict) else {}
                entry = (
                    active.get(str(key).casefold())
                    if isinstance(active, dict)
                    else None
                )
                chat_id = str(payload.get("chat_id") or "")
                if participating_for_chat(
                    config,
                    chat_id,
                    key,
                    entry if isinstance(entry, dict) else None,
                ):
                    return {
                        "ok": True,
                        "result": {
                            "suppressed": True,
                            "reason": "personal_event_participation_already_marked",
                            "chat_id": chat_id,
                            "wheel_key": key,
                        },
                    }
        return original_api(method, payload)

    monitor_module.telegram_api = telegram_api_filtered
    monitor_module._bbvg_personal_reminder_filter_installed = True


def self_test() -> None:
    config = {
        "owner_id": "1",
        "admins": [],
        "users": {
            "1": {"chat_id": "10", "participating_wheels": {}},
            "2": {
                "chat_id": "20",
                "participating_wheels": {
                    "wheel-a#action:10": {
                        "wheel_key": "wheel-a",
                        "action_id": 10,
                        "joined_at": "now",
                    }
                },
            },
            "3": {"chat_id": "30", "participating_wheels": {}},
        },
    }
    assert participating_for_chat(config, "20", "wheel-a", {"action_id": 10})
    assert not participating_for_chat(config, "20", "wheel-a", {"action_id": 11})
    assert not participating_for_chat(config, "30", "wheel-a", {"action_id": 10})
    set_global_participating("wheel-a", True)
    assert not participating_for_chat(config, "10", "wheel-a", {"action_id": 10})
    print("personal reminder filter self-test passed")


if __name__ == "__main__":
    self_test()
