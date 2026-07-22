from __future__ import annotations

import json
from typing import Any

import bbvg_monitor_runtime as runtime
import betboom_auto_participation


SUCCESS_STATUSES = {"participated", "already_participating"}


def _defer_failure_notification(monitor: Any, entry: dict[str, Any], result: Any) -> tuple[bool, str]:
    """The independent recovery step must get the final chance before alarming the user."""

    return False, "deferred_to_recovery"


def _event_versions(state: dict[str, Any]) -> dict[str, tuple[str, str]]:
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return {}
    result: dict[str, tuple[str, str]] = {}
    for token, raw in events.items():
        if not isinstance(raw, dict):
            continue
        result[str(token)] = (
            str(raw.get("status") or ""),
            str(raw.get("attempted_at") or raw.get("recorded_at") or ""),
        )
    return result


def _queue_new_successes(
    state: dict[str, Any],
    before: dict[str, tuple[str, str]],
    now_text: str,
) -> int:
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return 0
    queued = 0
    for token, raw in events.items():
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "").casefold()
        version = (
            str(raw.get("status") or ""),
            str(raw.get("attempted_at") or raw.get("recorded_at") or ""),
        )
        if before.get(str(token)) == version:
            continue
        if status not in SUCCESS_STATUSES or raw.get("bot_success_pending_at"):
            continue
        raw["bot_success_pending_at"] = now_text
        raw["bot_success_sync_status"] = "waiting_for_control_center"
        raw["bot_success_sync_version"] = 1
        queued += 1
    return queued


def main() -> int:
    monitor = runtime.monitor
    state = runtime.load_state_without_pending()
    event_versions_before = _event_versions(state)

    # The event worker is only the first browser path. A failure here is not final:
    # auto_participation_recovery.py runs immediately afterwards with an independent
    # scanner/browser. Do not send a false manual-action alert before that recovery
    # path has had its chance (the hooch07 incident exposed this race).
    original_notify = betboom_auto_participation._notify_manual_participation
    betboom_auto_participation._notify_manual_participation = _defer_failure_notification
    try:
        result = betboom_auto_participation.process_new_wheel_events(state, monitor)
    finally:
        betboom_auto_participation._notify_manual_participation = original_notify

    queued_successes = _queue_new_successes(
        state,
        event_versions_before,
        monitor.now_utc().isoformat(),
    )
    result["success_outcomes_queued"] = queued_successes
    result["debug_active_wheels"] = len(state.get("active_wheels", {}))
    result["debug_events"] = len(state.get("auto_participation_events", {}))
    result["debug_configured"] = betboom_auto_participation.configured()
    result["failure_alert_policy"] = "deferred_to_recovery"
    if bool(result.get("changed")) or queued_successes:
        monitor.save_state(state)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
