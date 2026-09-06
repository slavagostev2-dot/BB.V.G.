from __future__ import annotations

import json
import os
from typing import Any

import bbvg_monitor_runtime as runtime
import betboom_auto_participation
import betboom_participation_browser
import betboom_profile_identity
from bbvg.storage.event_payload import materialize_event_payload


SUCCESS_STATUSES = {"participated", "already_participating"}


def load_dispatched_event_payload(
    state: dict[str, Any],
    monitor: Any,
    raw_payload: str | None = None,
) -> str:
    """Materialize one explicitly dispatched event in the workflow checkout."""

    raw = raw_payload
    if raw is None:
        raw = os.getenv("BBVG_EVENT_PAYLOAD_JSON", "")
    if not str(raw or "").strip():
        return ""
    return materialize_event_payload(
        state,
        str(raw),
        received_at=monitor.now_utc(),
    )


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


def _primary_auth_revision() -> str:
    report = betboom_profile_identity.assert_account_slot_distinct(
        betboom_auto_participation.PRIMARY_ACCOUNT_KEY
    )
    return betboom_profile_identity.account_auth_revision(
        betboom_auto_participation.PRIMARY_ACCOUNT_KEY,
        report,
    )


def _primary_event_tokens(token: str) -> tuple[str, str]:
    return (
        token,
        f"{token}#account:{betboom_auto_participation.PRIMARY_ACCOUNT_KEY}",
    )


def _prepare_primary_auth_revision(
    state: dict[str, Any],
    auth_revision: str,
) -> tuple[bool, int, int]:
    """Invalidate event outcomes that belong to another BetBoom profile.

    Legacy baseline rows are not participation claims and must never turn into a
    surprise click merely because auth revisions were introduced later. They are
    stamped in place. Every other old/mismatched row is removed so the current
    profile gets its own browser check while the wheel is still active.
    """

    active = state.get("active_wheels")
    events = state.get("auto_participation_events")
    if not isinstance(active, dict) or not isinstance(events, dict):
        return False, 0, 0
    changed = False
    rearmed = 0
    baselines = 0
    for raw_key, entry in active.items():
        if not isinstance(entry, dict):
            continue
        key = str(raw_key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
        token = betboom_auto_participation._event_token(key, entry)
        if not token:
            continue
        stale_found = False
        for candidate in _primary_event_tokens(token):
            previous = events.get(candidate)
            if not isinstance(previous, dict):
                continue
            previous_revision = str(previous.get("auth_revision") or "")
            if previous_revision == auth_revision:
                continue
            if str(previous.get("status") or "").casefold() == "baseline_existing":
                previous["auth_revision"] = auth_revision
                baselines += 1
                changed = True
                continue
            events.pop(candidate, None)
            stale_found = True
            changed = True
        if not stale_found:
            continue
        rearmed += 1
        for field in (
            "auto_participation_status",
            "auto_participation_checked_at",
            "auto_participation_retry_allowed",
            "auto_participation_error",
            "auto_participation_confirmed_at",
            "auto_participation_origin",
        ):
            entry.pop(field, None)
        entry["auto_participation_auth_rearmed_at"] = runtime.monitor.now_utc().isoformat()
        entry["auto_participation_auth_rearm_reason"] = "betboom_profile_revision_changed"
    state.setdefault("auto_participation_auth_revisions", {})[
        betboom_auto_participation.PRIMARY_ACCOUNT_KEY
    ] = auth_revision
    return changed, rearmed, baselines


def _stamp_primary_auth_revision(
    state: dict[str, Any],
    auth_revision: str,
) -> bool:
    active = state.get("active_wheels")
    events = state.get("auto_participation_events")
    if not isinstance(active, dict) or not isinstance(events, dict):
        return False
    changed = False
    for raw_key, entry in active.items():
        if not isinstance(entry, dict):
            continue
        key = str(raw_key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
        token = betboom_auto_participation._event_token(key, entry)
        if not token:
            continue
        for candidate in _primary_event_tokens(token):
            record = events.get(candidate)
            if not isinstance(record, dict):
                continue
            if betboom_auto_participation._record_account_key(candidate, record) != (
                betboom_auto_participation.PRIMARY_ACCOUNT_KEY
            ):
                continue
            if str(record.get("auth_revision") or "") == auth_revision:
                continue
            record["auth_revision"] = auth_revision
            changed = True
    return changed


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


def _run_exact_primary_attempt(state: dict[str, Any], monitor: Any) -> dict[str, Any]:
    """Run event processing with the exact-label Playwright implementation.

    The legacy direct browser keeps compatibility helpers used elsewhere, but
    production must not classify arbitrary wheel-rules text as a successful
    participation result. The exact browser accepts only visible, standalone
    confirmation labels and clicks only exact participation controls.
    """

    original_participate = betboom_auto_participation.participate
    original_notify = betboom_auto_participation._notify_manual_participation
    betboom_auto_participation.participate = betboom_participation_browser.participate
    betboom_auto_participation._notify_manual_participation = _defer_failure_notification
    try:
        return dict(
            betboom_auto_participation.process_new_wheel_events(state, monitor)
        )
    finally:
        betboom_auto_participation.participate = original_participate
        betboom_auto_participation._notify_manual_participation = original_notify


def main() -> int:
    monitor = runtime.monitor
    state = runtime.load_state_without_pending()
    dispatched_event_id = load_dispatched_event_payload(state, monitor)
    betboom_auto_participation.canonicalize_primary_event_aliases(state)
    auth_revision = _primary_auth_revision()
    auth_changed, auth_rearmed, auth_baselines = _prepare_primary_auth_revision(
        state,
        auth_revision,
    )
    event_versions_before = _event_versions(state)

    # The event worker is only the first browser path. A failure here is not final:
    # auto_participation_recovery.py runs immediately afterwards with an independent
    # scanner/browser. Do not send a false manual-action alert before that recovery
    # path has had its chance (the hooch07 incident exposed this race).
    result = _run_exact_primary_attempt(state, monitor)
    auth_stamped = _stamp_primary_auth_revision(state, auth_revision)

    queued_successes = _queue_new_successes(
        state,
        event_versions_before,
        monitor.now_utc().isoformat(),
    )
    result["success_outcomes_queued"] = queued_successes
    result["auth_revision"] = auth_revision
    result["auth_rearmed"] = auth_rearmed
    result["auth_baselines_stamped"] = auth_baselines
    result["debug_active_wheels"] = len(state.get("active_wheels", {}))
    result["debug_events"] = len(state.get("auto_participation_events", {}))
    result["debug_configured"] = betboom_auto_participation.configured()
    result["browser_policy"] = "exact_visible_confirmation"
    result["failure_alert_policy"] = "deferred_to_recovery"
    result["dispatched_event_id"] = dispatched_event_id
    if bool(result.get("changed")) or queued_successes or auth_changed or auth_stamped:
        monitor.save_state(state)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())