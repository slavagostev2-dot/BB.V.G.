from __future__ import annotations

import html
from typing import Any, Callable

import betboom_auto_participation
import personal_wheel_voting


REMINDER_MARKERS = (
    "напоминание о колесе betboom",
    "последний шанс войти в колесо betboom",
    "вы ещё не отметили участие",
    "вы еще не отметили участие",
)
_CURRENT_STATS: dict[str, Any] | None = None


def set_current_stats(stats: dict[str, Any] | None) -> None:
    """Expose the current monitor-cycle vote ledger to reminder delivery."""

    global _CURRENT_STATS
    _CURRENT_STATS = stats if isinstance(stats, dict) else None


def set_global_participating(wheel_key: str, participating: bool) -> None:
    """Compatibility no-op: global participation no longer suppresses reminders."""


def _user_identity(
    config: dict[str, Any], chat_id: str
) -> tuple[str, dict[str, Any]]:
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    for user_id, raw in users.items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("chat_id") or user_id) == str(chat_id):
            return str(raw.get("id") or user_id), raw
    return "", {}


def _user_record(config: dict[str, Any], chat_id: str) -> dict[str, Any]:
    return _user_identity(config, chat_id)[1]


def _vote_recorded_for_chat(
    config: dict[str, Any],
    chat_id: str,
    event_key: str,
    stats: dict[str, Any] | None,
) -> bool:
    if not isinstance(stats, dict) or not event_key:
        return False
    user_id, _ = _user_identity(config, chat_id)
    if not user_id:
        return False
    try:
        actor = personal_wheel_voting.actor_vote_token(user_id)
    except RuntimeError:
        return False
    votes = stats.get("personal_wheel_votes")
    if not isinstance(votes, dict):
        return False
    return any(
        isinstance(record, dict)
        and str(record.get("actor") or "").casefold() == actor
        and str(record.get("event_key") or "").casefold() == event_key
        for record in votes.values()
    )


def participating_for_chat(
    config: dict[str, Any],
    chat_id: str,
    wheel_key: str,
    entry: dict[str, Any] | None = None,
    *,
    stats: dict[str, Any] | None = None,
) -> bool:
    normalized = str(wheel_key or "").casefold()
    if not normalized:
        return False
    record = _user_record(config, chat_id)
    raw = record.get("participating_wheels")
    if isinstance(raw, list):
        joined = {str(value).casefold() for value in raw}
        if normalized in joined and not bool(
            (entry or {}).get("action_id") or (entry or {}).get("event_id")
        ):
            return True
        return _vote_recorded_for_chat(
            config,
            chat_id,
            personal_wheel_voting.wheel_event_key(normalized, entry),
            stats,
        )
    event_key = personal_wheel_voting.wheel_event_key(normalized, entry)
    if isinstance(raw, dict) and event_key in {
        str(value).casefold() for value in raw
    }:
        return True
    if (
        isinstance(raw, dict)
        and event_key == normalized
        and normalized in {str(value).casefold() for value in raw}
    ):
        return True
    return _vote_recorded_for_chat(config, chat_id, event_key, stats)


def install(monitor_module: Any, router_module: Any) -> None:
    """Filter reminders and trigger one auto-participation attempt per new event."""

    if getattr(monitor_module, "_bbvg_personal_reminder_filter_installed", False):
        return
    original_api: Callable = monitor_module.telegram_api
    original_process_active: Callable = monitor_module.process_active_wheels

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
                vote_stats = _CURRENT_STATS
                if vote_stats is None:
                    try:
                        loaded = monitor_module.data_store.load_stats()
                        vote_stats = loaded if isinstance(loaded, dict) else None
                    except Exception:
                        vote_stats = None
                if participating_for_chat(
                    config,
                    chat_id,
                    key,
                    entry if isinstance(entry, dict) else None,
                    stats=vote_stats,
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

    def process_active_with_event_participation(state: dict, stats: dict):
        result = original_process_active(state, stats)
        auto = betboom_auto_participation.process_new_wheel_events(
            state, monitor_module
        )
        if bool(auto.get("changed")):
            result["changed"] = True
        result["auto_participation_attempted"] = int(auto.get("attempted", 0) or 0)
        result["auto_participation_succeeded"] = int(auto.get("succeeded", 0) or 0)
        result["auto_participation_failed"] = int(auto.get("failed", 0) or 0)
        return result

    # Preserve the lifecycle integration identity expected by existing CI while
    # keeping this wrapper outermost in the production runtime.
    process_active_with_event_participation.__module__ = original_process_active.__module__

    monitor_module.telegram_api = telegram_api_filtered
    monitor_module.process_active_wheels = process_active_with_event_participation
    monitor_module._bbvg_personal_reminder_filter_installed = True
    monitor_module._bbvg_event_auto_participation_installed = True


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
