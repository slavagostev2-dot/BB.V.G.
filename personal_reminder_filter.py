from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from datetime import timedelta
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
_DISPATCH_RETRY_AFTER = timedelta(minutes=3)
_DISPATCH_RESULT_TIMEOUT = timedelta(minutes=5)
_DISPATCH_FAILURE_LIMIT = 3
_DISABLED_WORKFLOW_MARKER = "disabled workflow"


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


def _merge_dispatch_ledger_from_disk(state: dict[str, Any], monitor_module: Any) -> bool:
    """Merge dispatcher-written status back into the monitor's in-memory state."""

    try:
        persisted = json.loads(monitor_module.STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return False
    if not isinstance(persisted, dict):
        return False
    ledger = persisted.get("auto_participation_dispatch_events")
    if not isinstance(ledger, dict):
        return False
    state["auto_participation_dispatch_events"] = ledger
    return True


def _recoverable_processed_failure(record: Any, entry: dict[str, Any]) -> str:
    if not isinstance(record, dict):
        return ""
    status = str(record.get("status") or entry.get("auto_participation_status") or "")
    if status == "workflow_dispatch_failed":
        detail = " ".join(
            str(value or "")
            for value in (
                record.get("detail"),
                record.get("dispatch_error"),
                entry.get("auto_participation_error"),
            )
        ).casefold()
        if _DISABLED_WORKFLOW_MARKER in detail:
            return "disabled_workflow"
    if status == "button_not_found":
        try:
            attempt_version = int(record.get("attempt_version", 1) or 1)
        except (TypeError, ValueError):
            attempt_version = 1
        target_version = int(
            getattr(betboom_auto_participation, "_PARTICIPATION_ATTEMPT_VERSION", 1)
            or 1
        )
        if attempt_version < target_version:
            return "browser_attempt_upgrade"
    return ""


def _rearm_recoverable_failure(
    state: dict[str, Any],
    monitor_module: Any,
    processed: dict[str, Any],
    dispatched: dict[str, Any],
    token: str,
    wheel_key: str,
    entry: dict[str, Any],
    current: Any,
) -> bool:
    """Reopen only still-active events covered by an explicit recovery rule."""

    record = processed.get(token)
    reason = _recoverable_processed_failure(record, entry)
    if not reason:
        return False
    if not betboom_auto_participation._eligible_for_event_attempt(
        entry, monitor_module, current
    ):
        return False

    processed.pop(token, None)
    dispatched.pop(token, None)
    for field in (
        "auto_participation_status",
        "auto_participation_checked_at",
        "auto_participation_retry_allowed",
        "auto_participation_error",
        "auto_participation_manual_notification_error",
    ):
        entry.pop(field, None)
    entry["auto_participation_rearmed_at"] = current.isoformat()
    entry["auto_participation_rearm_reason"] = reason
    print(
        "Rearmed auto participation after recoverable event failure: "
        f"wheel={wheel_key} token={token} reason={reason}"
    )
    return True


def _record_dispatch_failure(
    state: dict[str, Any],
    monitor_module: Any,
    token: str,
    wheel_key: str,
    *,
    status: str,
    detail: str,
) -> bool:
    """Retry ordinary dispatch failures silently before escalating to the owner."""

    current = monitor_module.now_utc()
    active = state.setdefault("active_wheels", {})
    entry = active.get(wheel_key)
    if not isinstance(entry, dict):
        entry = {"wheel_key": wheel_key, "identifier": wheel_key}
        active[wheel_key] = entry

    dispatches = state.setdefault("auto_participation_dispatch_events", {})
    dispatch_record = dispatches.get(token)
    if not isinstance(dispatch_record, dict):
        dispatch_record = {"wheel_key": wheel_key}
        dispatches[token] = dispatch_record

    try:
        previous_failures = int(dispatch_record.get("failure_count", 0) or 0)
    except (TypeError, ValueError):
        previous_failures = 0
    failure_count = previous_failures + 1
    dispatch_record["failure_count"] = failure_count

    if status in {"workflow_dispatch_failed", "workflow_dispatch_timeout"} and failure_count < _DISPATCH_FAILURE_LIMIT:
        retry_delay = (
            _DISPATCH_RETRY_AFTER
            if status == "workflow_dispatch_failed"
            else timedelta(minutes=1)
        )
        retry_at = current + retry_delay
        dispatch_record.update(
            {
                "wheel_key": wheel_key,
                "status": "workflow_dispatch_retry_wait",
                "dispatch_error": detail[:500],
                "last_failure_at": current.isoformat(),
                "retry_after_at": retry_at.isoformat(),
                "manual_notification_sent": False,
            }
        )
        dispatch_record.pop("alert_attempted_at", None)
        dispatch_record.pop("manual_notification_at", None)
        dispatch_record.pop("manual_notification_detail", None)
        entry["auto_participation_status"] = "workflow_dispatch_retry_wait"
        entry["auto_participation_checked_at"] = current.isoformat()
        entry["auto_participation_retry_allowed"] = True
        entry["auto_participation_error"] = detail[:300]
        entry.pop("auto_participation_manual_notification_error", None)
        print(
            "Auto participation dispatch will retry silently: "
            f"wheel={wheel_key} attempt={failure_count}/{_DISPATCH_FAILURE_LIMIT} "
            f"retry_at={retry_at.isoformat()}"
        )
        return False

    result = betboom_auto_participation.ParticipationResult(
        False,
        status,
        detail[:300],
    )
    notified, notification_detail = (
        betboom_auto_participation._notify_manual_participation(
            monitor_module,
            entry,
            result,
        )
    )

    dispatch_record["status"] = (
        f"{status}_notified" if notified else f"{status}_alert_pending"
    )
    dispatch_record["dispatch_error"] = detail[:500]
    dispatch_record["alert_attempted_at"] = current.isoformat()
    dispatch_record["manual_notification_sent"] = notified
    dispatch_record["manual_notification_detail"] = notification_detail[:300]

    entry["auto_participation_status"] = status
    entry["auto_participation_checked_at"] = current.isoformat()
    entry["auto_participation_retry_allowed"] = False
    entry["auto_participation_error"] = detail[:300]

    if notified:
        dispatch_record["manual_notification_at"] = current.isoformat()
        entry["auto_participation_manual_notification_at"] = current.isoformat()
        state.setdefault("auto_participation_events", {})[token] = {
            "wheel_key": wheel_key,
            "status": status,
            "detail": detail[:300],
            "dispatch_failed_at": current.isoformat(),
            "retry_allowed": False,
            "manual_notification_sent": True,
            "manual_notification_at": current.isoformat(),
            "manual_notification_detail": notification_detail[:300],
        }
    else:
        entry["auto_participation_manual_notification_error"] = (
            notification_detail[:300]
        )
    return notified


def _schedule_auto_participation_dispatch(state: dict[str, Any], monitor_module: Any) -> bool:
    """Persist new event requests and synchronously dispatch the isolated workflow."""

    if not os.getenv("GITHUB_TOKEN", "").strip() or not os.getenv(
        "GITHUB_REPOSITORY", ""
    ).strip():
        return False
    if not state.get("auto_participation_event_mode_initialized_at"):
        return False

    current = monitor_module.now_utc()
    processed = state.setdefault("auto_participation_events", {})
    dispatched = state.setdefault("auto_participation_dispatch_events", {})
    candidates: list[tuple[str, str, bool]] = []
    alerts: list[tuple[str, str, str, str]] = []
    changed = False

    for key, entry in list(state.setdefault("active_wheels", {}).items()):
        if not isinstance(entry, dict):
            continue
        normalized = str(key).casefold()
        token = betboom_auto_participation._event_token(normalized, entry)
        if not token:
            continue
        if token in processed:
            if _rearm_recoverable_failure(
                state,
                monitor_module,
                processed,
                dispatched,
                token,
                normalized,
                entry,
                current,
            ):
                changed = True
            else:
                continue

        previous_dispatch = dispatched.get(token)
        is_retry = isinstance(previous_dispatch, dict)
        if is_retry:
            previous_status = str(previous_dispatch.get("status") or "")
            previous_at = monitor_module.parse_datetime(
                previous_dispatch.get("alert_attempted_at")
                or previous_dispatch.get("last_failure_at")
                or previous_dispatch.get("dispatched_at")
                or previous_dispatch.get("scheduled_at")
            )
            if previous_status.endswith("_notified"):
                continue
            if previous_status == "workflow_dispatch_retry_wait":
                retry_at = monitor_module.parse_datetime(
                    previous_dispatch.get("retry_after_at")
                )
                if retry_at is not None and current < retry_at:
                    continue
            if previous_status.endswith("_alert_pending"):
                if previous_at is not None and current - previous_at < _DISPATCH_RETRY_AFTER:
                    continue
                base_status = previous_status.removesuffix("_alert_pending")
                alerts.append(
                    (
                        token,
                        normalized,
                        base_status,
                        str(previous_dispatch.get("dispatch_error") or "")
                        or "не удалось отправить уведомление о сбое автоучастия",
                    )
                )
                continue
            if previous_status == "workflow_dispatch_sent":
                if previous_at is not None and current - previous_at < _DISPATCH_RESULT_TIMEOUT:
                    continue
                alerts.append(
                    (
                        token,
                        normalized,
                        "workflow_dispatch_timeout",
                        "GitHub принял запуск автоучастия, но worker не записал результат за 5 минут",
                    )
                )
                continue

            scheduled_at = monitor_module.parse_datetime(
                previous_dispatch.get("scheduled_at")
            )
            if scheduled_at is not None and current - scheduled_at < _DISPATCH_RETRY_AFTER:
                continue

        if not betboom_auto_participation._eligible_for_event_attempt(
            entry, monitor_module, current
        ):
            continue
        candidates.append((token, normalized, is_retry))

    for token, normalized, status, detail in alerts:
        _record_dispatch_failure(
            state,
            monitor_module,
            token,
            normalized,
            status=status,
            detail=detail,
        )
        changed = True

    if not candidates:
        if changed:
            monitor_module.save_state(state)
        return changed

    retry_count = 0
    for token, normalized, is_retry in candidates:
        previous_record = dispatched.get(token) if is_retry else None
        try:
            failure_count = int(
                previous_record.get("failure_count", 0) if isinstance(previous_record, dict) else 0
            )
        except (TypeError, ValueError):
            failure_count = 0
        if is_retry:
            retry_count += 1
        dispatch_record = {
            "wheel_key": normalized,
            "scheduled_at": current.isoformat(),
            "status": (
                "workflow_dispatch_retry_scheduled"
                if is_retry
                else "workflow_dispatch_scheduled"
            ),
        }
        if failure_count:
            dispatch_record["failure_count"] = failure_count
        dispatched[token] = dispatch_record

    # The dispatcher must see the same state that will be pushed to GitHub.
    # Saving here is intentional: the child process commits state.json before
    # requesting workflow_dispatch, so the worker cannot start from stale data.
    monitor_module.save_state(state)
    try:
        completed = subprocess.run(
            [sys.executable, "auto_participation_dispatch.py"],
            cwd=str(monitor_module.STATE_PATH.parent),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as exc:
        detail = f"dispatcher_exception:{type(exc).__name__}: {exc}"[:500]
        for token, normalized, _ in candidates:
            _record_dispatch_failure(
                state,
                monitor_module,
                token,
                normalized,
                status="workflow_dispatch_failed",
                detail=detail,
            )
        monitor_module.save_state(state)
        print(f"WARNING synchronous auto participation dispatch failed: {detail}")
        return True

    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip())

    merged = _merge_dispatch_ledger_from_disk(state, monitor_module)
    failure_detail = ""
    if completed.returncode != 0:
        failure_detail = (
            f"dispatcher_exit_{completed.returncode}: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )[:500]
    elif not merged:
        failure_detail = "dispatcher завершился без читаемого статуса state.json"

    if failure_detail:
        for token, normalized, _ in candidates:
            _record_dispatch_failure(
                state,
                monitor_module,
                token,
                normalized,
                status="workflow_dispatch_failed",
                detail=failure_detail,
            )
        monitor_module.save_state(state)
        print(f"WARNING auto participation dispatcher failed: {failure_detail}")
        return True

    unconfirmed: list[tuple[str, str]] = []
    ledger = state.setdefault("auto_participation_dispatch_events", {})
    for token, normalized, _ in candidates:
        record = ledger.get(token)
        if not isinstance(record, dict) or str(record.get("status") or "") != "workflow_dispatch_sent":
            unconfirmed.append((token, normalized))

    if unconfirmed:
        detail = "dispatcher завершился без подтверждения workflow_dispatch_sent"
        for token, normalized in unconfirmed:
            _record_dispatch_failure(
                state,
                monitor_module,
                token,
                normalized,
                status="workflow_dispatch_failed",
                detail=detail,
            )
        monitor_module.save_state(state)
        print(f"WARNING auto participation dispatcher failed: {detail}")
        return True

    # Persist the child-written `workflow_dispatch_sent` status in the parent's
    # in-memory state so the monitor cannot overwrite it with stale `scheduled`.
    monitor_module.save_state(state)
    print(
        f"Queued auto participation workflow for {len(candidates)} new wheel event(s)"
        + (f"; retries={retry_count}" if retry_count else "")
    )
    return True


def install(monitor_module: Any, router_module: Any) -> None:
    """Filter reminders and dispatch participation immediately for new wheel events."""

    if getattr(monitor_module, "_bbvg_personal_reminder_filter_installed", False):
        return
    original_api: Callable = monitor_module.telegram_api
    original_process_active = getattr(monitor_module, "process_active_wheels", None)

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

    monitor_module.telegram_api = telegram_api_filtered
    monitor_module.process_auto_participation_dispatch = (
        lambda state: _schedule_auto_participation_dispatch(state, monitor_module)
    )

    if callable(original_process_active):
        def process_active_with_auto_dispatch(state: dict, stats: dict):
            result = original_process_active(state, stats)
            if _schedule_auto_participation_dispatch(state, monitor_module):
                result["changed"] = True
            return result

        # Preserve the production contract checked by monitor.yml: the installed
        # lifecycle remains formally attributed to wheel_lifecycle_v2.
        process_active_with_auto_dispatch.__module__ = original_process_active.__module__
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

    disabled = {
        "status": "workflow_dispatch_failed",
        "detail": "HTTP 422 Cannot trigger a workflow_dispatch on a disabled workflow",
    }
    assert _recoverable_processed_failure(disabled, {}) == "disabled_workflow"
    legacy_button = {"status": "button_not_found"}
    assert _recoverable_processed_failure(legacy_button, {}) == "browser_attempt_upgrade"
    current_button = {
        "status": "button_not_found",
        "attempt_version": betboom_auto_participation._PARTICIPATION_ATTEMPT_VERSION,
    }
    assert _recoverable_processed_failure(current_button, {}) == ""
    print("personal reminder filter self-test passed")


if __name__ == "__main__":
    self_test()
