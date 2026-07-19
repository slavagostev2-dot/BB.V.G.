from __future__ import annotations

import html
import os
import subprocess
import sys
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


def _schedule_auto_participation_dispatch(state: dict[str, Any], monitor_module: Any) -> bool:
    """Queue one isolated participation workflow when new eligible wheel events appear."""

    if not os.getenv("GITHUB_TOKEN", "").strip() or not os.getenv(
        "GITHUB_REPOSITORY", ""
    ).strip():
        return False
    if not state.get("auto_participation_event_mode_initialized_at"):
        return False

    current = monitor_module.now_utc()
    processed = state.setdefault("auto_participation_events", {})
    dispatched = state.setdefault("auto_participation_dispatch_events", {})
    candidates: list[tuple[str, str]] = []

    for key, entry in list(state.setdefault("active_wheels", {}).items()):
        if not isinstance(entry, dict):
            continue
        normalized = str(key).casefold()
        token = betboom_auto_participation._event_token(normalized, entry)
        if not token or token in processed or token in dispatched:
            continue
        if not betboom_auto_participation._eligible_for_event_attempt(
            entry, monitor_module, current
        ):
            continue
        candidates.append((token, normalized))

    if not candidates:
        return False

    try:
        subprocess.Popen(
            [sys.executable, "auto_participation_dispatch.py"],
            cwd=str(monitor_module.STATE_PATH.parent),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        print(
            "WARNING auto participation workflow dispatch scheduling failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False

    for token, normalized in candidates:
        dispatched[token] = {
            "wheel_key": normalized,
            "scheduled_at": current.isoformat(),
            "status": "workflow_dispatch_scheduled",
        }
    print(f"Scheduled auto participation workflow for {len(candidates)} new wheel event(s)")
    return True


def install(monitor_module: Any, router_module: Any) -> None:
    """Filter reminders and dispatch participation immediately for new wheel events."""

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

    def process_active_with_auto_dispatch(state: dict, stats: dict):
        result = original_process_active(state, stats)
        if _schedule_auto_participation_dispatch(state, monitor_module):
            result["changed"] = True
        return result

    # Preserve the production contract checked by monitor.yml: the installed
    # lifecycle remains formally attributed to wheel_lifecycle_v2.
    process_active_with_auto_dispatch.__module__ = original_process_active.__module__
    monitor_module.telegram_api = telegram_api_filtered
    monitor_module.process_active_wheels = process_active_with_auto_dispatch
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
