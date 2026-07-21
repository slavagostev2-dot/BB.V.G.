from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import betboom_auto_participation as auto
import betboom_participation_browser
import monitor

ROOT = Path(__file__).resolve().parent


def _json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def _event_token(item: dict[str, Any]) -> str:
    key = str(item.get("wheel_key") or "").casefold()
    try:
        action_id = int(item.get("action_id") or 0)
    except (TypeError, ValueError):
        action_id = 0
    start = str(item.get("server_start_at") or "")
    if action_id:
        return f"{key}#action:{action_id}:{start}"
    return f"{key}#seen:{item.get('message_date') or ''}"


def _confirmed_success_for_event(
    state: dict[str, Any],
    item: dict[str, Any],
) -> bool:
    """Return True only when this exact wheel event was already confirmed participating."""

    key = str(item.get("wheel_key") or "").casefold()
    if not key:
        return False
    token = _event_token(item)

    processed = state.get("auto_participation_events")
    record = processed.get(token) if isinstance(processed, dict) else None
    if isinstance(record, dict) and str(record.get("status") or "") in {
        "participated",
        "already_marked_participating",
    }:
        return True

    active = state.get("active_wheels")
    entry = active.get(key) if isinstance(active, dict) else None
    if isinstance(entry, dict) and _event_token(entry) == token:
        if bool(entry.get("participating")):
            return True
        if str(entry.get("auto_participation_status") or "") == "participated":
            return True
        if entry.get("auto_participation_confirmed_at"):
            return True

    return False


def _ensure_button_context(
    state: dict[str, Any],
    entry: dict[str, Any],
    item: dict[str, Any],
) -> None:
    """Restore callback context when recovery had to recreate active state."""

    source = str(entry.get("source") or item.get("source") or "").strip()
    try:
        message_id = int(entry.get("message_id") or item.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    message_date = monitor.parse_datetime(
        entry.get("message_date") or item.get("message_date")
    )
    message_url = str(
        entry.get("message_url") or item.get("message_url") or ""
    ).strip()
    message_text = str(
        entry.get("message_text")
        or item.get("message_text")
        or entry.get("url")
        or item.get("url")
        or ""
    )
    url = str(entry.get("url") or item.get("url") or "").strip()
    if not source or message_id <= 0 or message_date is None or not url:
        return

    message = monitor.Message(
        source=source,
        message_id=message_id,
        date=message_date,
        text=message_text,
        message_url=message_url,
    )
    token = monitor.register_button_context(
        state,
        message,
        url,
        status=str(entry.get("status") or "preliminary"),
        method=str(entry.get("method") or "recovery BetBoom"),
        page_excerpt=str(entry.get("page_excerpt") or ""),
    )
    entry["button_token"] = token


def _failure_record(
    previous: dict[str, Any] | None,
    *,
    key: str,
    status: str,
    detail: str,
    scanned_at: Any,
) -> dict[str, Any]:
    """Build a durable failure candidate without sending Telegram from recovery."""

    record: dict[str, Any] = {
        "wheel_key": key,
        "status": status,
        "detail": detail[:300],
        "attempted_at": scanned_at.isoformat(),
        "retry_allowed": False,
        "recovery_scan": True,
    }
    if isinstance(previous, dict):
        for field in (
            "manual_notification_sent",
            "manual_notification_detail",
            "manual_notification_at",
            "bot_failure_pending_at",
            "bot_failure_sync_status",
            "bot_failure_sync_version",
            "bot_failure_status",
            "bot_failure_detail",
        ):
            if field in previous:
                record[field] = previous[field]

    # Legacy recovery may already have sent a failure before this architecture was
    # introduced. Never queue a second failure for that exact event.
    if not bool(record.get("manual_notification_sent")):
        record.setdefault("bot_failure_pending_at", scanned_at.isoformat())
        record["bot_failure_sync_status"] = "waiting_for_control_center"
        record["bot_failure_sync_version"] = 1
        record["bot_failure_status"] = status
        record["bot_failure_detail"] = detail[:300]
    return record


def _restore_runtime_state(
    state: dict[str, Any],
    active: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    scanned_at: Any,
) -> None:
    attempts_by_key = {
        str(item.get("wheel_key") or "").casefold(): item
        for item in attempts
        if isinstance(item, dict)
    }
    active_wheels = state.setdefault("active_wheels", {})
    participating_wheels = state.setdefault("participating_wheels", {})
    processed = state.setdefault("auto_participation_events", {})

    for item in active:
        key = str(item.get("wheel_key") or "").casefold()
        if not key:
            continue
        existing = active_wheels.get(key)
        is_recovered_missing = not isinstance(existing, dict)
        entry: dict[str, Any] = {} if is_recovered_missing else existing
        if is_recovered_missing:
            active_wheels[key] = entry

        # Capture success before refreshing API fields. A later browser probe is not
        # allowed to downgrade an exact event already confirmed by BetBoom.
        confirmed_before = _confirmed_success_for_event(state, item)

        deadline = monitor.parse_datetime(item.get("deadline"))
        expires = (
            deadline + timedelta(minutes=30)
            if deadline is not None
            else scanned_at + timedelta(hours=2)
        )

        if is_recovered_missing:
            entry.update(
                {
                    "identifier": key,
                    "wheel_key": key,
                    "url": str(item.get("url") or ""),
                    "source": str(item.get("source") or ""),
                    "message_id": int(item.get("message_id") or 0),
                    "message_date": item.get("message_date"),
                    "message_url": item.get("message_url"),
                    "message_text": str(item.get("message_text") or "")[:4000],
                    "method": "восстановлено recovery-проверкой BetBoom",
                }
            )
        else:
            entry.setdefault("identifier", key)
            entry.setdefault("wheel_key", key)
            entry.setdefault("url", str(item.get("url") or ""))
            if not entry.get("message_text") and item.get("message_text"):
                entry["message_text"] = str(item.get("message_text") or "")[:4000]

        try:
            action_id = int(item.get("action_id") or 0)
        except (TypeError, ValueError):
            action_id = 0
        entry.update(
            {
                "action_id": action_id,
                "deadline": item.get("deadline"),
                "expires_at": expires.isoformat(),
                "server_start_at": item.get("server_start_at"),
                "page_status": "active",
                "availability_status": "available",
                "verification_status": monitor.WHEEL_VERIFICATION_CONFIRMED,
                "last_checked_at": scanned_at.isoformat(),
                "last_verification_at": scanned_at.isoformat(),
                "needs_manual_time": deadline is None,
            }
        )
        _ensure_button_context(state, entry, item)

        attempt = attempts_by_key.get(key)
        if not isinstance(attempt, dict):
            entry.setdefault("participating", False)
            entry.setdefault("lifecycle_state", "active")
            continue

        token = _event_token(item)
        if not bool(attempt.get("success")):
            if confirmed_before:
                entry["participating"] = True
                entry["lifecycle_state"] = "participating"
                entry["auto_participation_status"] = "participated"
                entry["auto_participation_retry_allowed"] = False
                entry.pop("auto_participation_error", None)
                entry.pop("auto_participation_manual_notification_error", None)
                continue

            status = str(attempt.get("status") or "failed")
            detail = str(
                attempt.get("detail") or "автоучастие не подтверждено"
            )[:300]
            previous = processed.get(token)
            processed[token] = _failure_record(
                previous if isinstance(previous, dict) else None,
                key=key,
                status=status,
                detail=detail,
                scanned_at=scanned_at,
            )
            entry.update(
                {
                    "participating": False,
                    "lifecycle_state": "active",
                    "auto_participation_status": status,
                    "auto_participation_checked_at": scanned_at.isoformat(),
                    "auto_participation_retry_allowed": False,
                    "auto_participation_error": detail,
                }
            )
            continue

        status = str(attempt.get("status") or "participated")
        if status == "already_marked_participating":
            entry["participating"] = True
            entry["lifecycle_state"] = "participating"
            entry["auto_participation_status"] = "participated"
            entry["auto_participation_retry_allowed"] = False
            entry.pop("auto_participation_error", None)
            entry.pop("auto_participation_manual_notification_error", None)
            continue

        entry.update(
            {
                "participating": True,
                "participating_at": scanned_at.isoformat(),
                "lifecycle_state": "participating",
                "auto_participation_status": "participated",
                "auto_participation_checked_at": scanned_at.isoformat(),
                "auto_participation_confirmed_at": scanned_at.isoformat(),
                "auto_participation_retry_allowed": False,
            }
        )
        entry.pop("auto_participation_error", None)
        entry.pop("auto_participation_manual_notification_error", None)
        participating_wheels[key] = {
            "identifier": key,
            "url": str(item.get("url") or ""),
            "deadline": item.get("deadline"),
            "expires_at": expires.isoformat(),
            "marked_at": scanned_at.isoformat(),
            "confirmed_at": scanned_at.isoformat(),
            "participation_source": "betboom_browser_recovery",
            "participation_status": status,
        }
        previous = processed.get(token)
        success_record: dict[str, Any] = {
            "wheel_key": key,
            "status": "participated",
            "detail": str(
                attempt.get("detail") or "BetBoom подтвердил участие"
            )[:300],
            "attempted_at": scanned_at.isoformat(),
            "retry_allowed": False,
            "recovery_scan": True,
        }
        if isinstance(previous, dict):
            for field in (
                "bot_success_pending_at",
                "bot_success_sync_status",
                "bot_success_sync_version",
            ):
                if field in previous:
                    success_record[field] = previous[field]
        processed[token] = success_record

    state["last_auto_participation_recovery_scan_at"] = scanned_at.isoformat()
    monitor.save_state(state)


def run_recovery() -> dict[str, Any]:
    """Find fresh approved wheels, verify them with BetBoom, and recover participation."""

    if not auto.configured():
        raise RuntimeError("BetBoom auto participation session is not configured")

    sources = monitor.read_list(monitor.SOURCES_PATH)
    results, errors, empty = monitor.fetch_all_sources(sources)
    now = monitor.now_utc()
    cutoff = now - timedelta(hours=3)

    persisted = _json(monitor.STATE_PATH, {})

    candidates: dict[str, dict[str, Any]] = {}
    for source, messages in results.items():
        if not isinstance(messages, list):
            continue
        for message in messages:
            try:
                published = message.date.astimezone(monitor.UTC)
            except Exception:
                continue
            if published < cutoff:
                continue
            for link in monitor.extract_links(message.text):
                key = monitor.wheel_key(link)
                current = candidates.get(key)
                record = {
                    "wheel_key": key,
                    "url": monitor.normalize_url(link),
                    "source": source,
                    "message_id": message.message_id,
                    "message_date": published.isoformat(),
                    "message_url": message.message_url,
                    "message_text": str(message.text or "")[:4000],
                }
                if current is None or record["message_date"] > current["message_date"]:
                    candidates[key] = record

    checked: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for record in sorted(
        candidates.values(), key=lambda item: item["message_date"], reverse=True
    ):
        inspection = monitor.inspect_wheel_page(record["url"])
        item = dict(record)
        item.update(
            api_status=inspection.status,
            action_id=inspection.action_id,
            deadline=inspection.deadline.isoformat() if inspection.deadline else None,
            server_start_at=(
                inspection.server_start_at.isoformat()
                if inspection.server_start_at
                else None
            ),
        )
        checked.append(item)
        if inspection.status == "active":
            active.append(item)

    attempts: list[dict[str, Any]] = []
    for item in active:
        if _confirmed_success_for_event(persisted, item):
            attempts.append(
                {
                    **item,
                    "success": True,
                    "status": "already_marked_participating",
                    "detail": (
                        "Участие уже подтверждено для этого события; "
                        "повторный клик не требуется"
                    ),
                }
            )
            continue

        result = betboom_participation_browser.participate(str(item["url"]))
        attempts.append(
            {
                **item,
                "success": bool(result.success),
                "status": str(result.status),
                "detail": str(result.detail)[:300],
            }
        )

    _restore_runtime_state(persisted, active, attempts, now)
    return {
        "scanned_at": now.isoformat(),
        "sources_total": len(sources),
        "sources_ok": len(results),
        "source_errors": len(errors),
        "source_empty": len(empty),
        "fresh_candidates": len(candidates),
        "active_candidates": len(active),
        "checked": checked,
        "attempts": attempts,
        # Kept for backward-compatible workflow/result consumers. Recovery no longer
        # sends final user-facing failures; Control Center is the sole outcome sender.
        "failure_notifications": [],
        "failure_delivery_policy": "control_center_authoritative",
        "successful_urls": [item["url"] for item in attempts if item.get("success")],
    }


def self_test() -> None:
    item = {
        "wheel_key": "ctom10",
        "action_id": 947,
        "server_start_at": "2026-07-21T14:23:41.383000+00:00",
        "message_date": "2026-07-21T14:24:14+00:00",
    }
    token = _event_token(item)
    state = {
        "active_wheels": {
            "ctom10": {
                **item,
                "participating": True,
                "auto_participation_status": "participated",
                "auto_participation_confirmed_at": "2026-07-21T14:25:07.318058+00:00",
            }
        },
        "participating_wheels": {},
        "auto_participation_events": {
            token: {
                "wheel_key": "ctom10",
                "status": "participated",
                "attempted_at": "2026-07-21T14:25:07.318058+00:00",
            }
        },
    }
    assert _confirmed_success_for_event(state, item)

    next_generation = {
        **item,
        "action_id": 948,
        "server_start_at": "2026-07-21T15:00:00+00:00",
    }
    assert not _confirmed_success_for_event(state, next_generation)

    state_without_exact_success = {
        "active_wheels": {},
        "participating_wheels": {"ctom10": {"confirmed_at": "old"}},
        "auto_participation_events": {},
    }
    assert not _confirmed_success_for_event(state_without_exact_success, item)

    class _Moment:
        @staticmethod
        def isoformat() -> str:
            return "2026-07-21T15:00:00+00:00"

    failure = _failure_record(
        None,
        key="ctom11",
        status="unconfirmed",
        detail="элемент участия нажат, но подтверждение не найдено",
        scanned_at=_Moment(),
    )
    assert failure["bot_failure_sync_status"] == "waiting_for_control_center"
    assert failure["bot_failure_pending_at"] == "2026-07-21T15:00:00+00:00"
    assert not failure.get("manual_notification_sent")

    legacy_failure = _failure_record(
        {"manual_notification_sent": True, "manual_notification_at": "old"},
        key="ctom11",
        status="unconfirmed",
        detail="still unconfirmed",
        scanned_at=_Moment(),
    )
    assert "bot_failure_pending_at" not in legacy_failure
    print("auto participation recovery authoritative-outcome self-test passed")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    payload = run_recovery()
    print(json.dumps(payload, ensure_ascii=False))
    # A clean scan with no new active wheel is not a workflow failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
