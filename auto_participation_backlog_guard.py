from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import auto_participation_notifications as notifications
import personal_wheel_voting

UTC = timezone.utc
MAX_USER_NOTIFICATION_DELAY = timedelta(minutes=20)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _latest_pending_at(
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> datetime | None:
    timestamps: list[datetime] = []
    for _token, record, _success in accounts.values():
        for field in (
            "bot_success_pending_at",
            "bot_failure_pending_at",
            "attempted_at",
        ):
            parsed = _parse_datetime(record.get(field))
            if parsed is not None:
                timestamps.append(parsed)
                break
    return max(timestamps, default=None)


def _is_stale(
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
    *,
    now: datetime | None = None,
) -> bool:
    pending_at = _latest_pending_at(accounts)
    if pending_at is None:
        return True
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return current - pending_at > MAX_USER_NOTIFICATION_DELAY


def _trim(records: dict[str, dict[str, Any]], limit: int) -> dict[str, dict[str, Any]]:
    if len(records) <= limit:
        return records
    return dict(list(records.items())[-limit:])


def close_stale_backlog(panel: Any, *, now: datetime | None = None) -> int:
    """Close old settled outcomes without Telegram delivery or rating changes."""

    snap = panel.snapshot()
    state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
    groups = notifications._settled_event_groups(state, now=now)
    if not groups:
        return 0

    owner_sync = notifications.auto_participation_owner_sync
    access, owner_id, owner, _owner_chat_id = owner_sync._owner_context(panel)
    success_records = owner_sync._completion_records(owner)
    failure_records = owner_sync._failure_records(owner)
    closed = 0
    current = (now or datetime.now(UTC)).astimezone(UTC)

    for base_token, accounts in sorted(groups.items()):
        if not _is_stale(accounts, now=current):
            continue
        primary_record = accounts[notifications.PRIMARY_ACCOUNT_KEY][1]
        key = str(primary_record.get("wheel_key") or "").casefold()
        item, active_matches = notifications._event_item(state, base_token, accounts)
        if not key or not isinstance(item, dict):
            continue
        event_key = personal_wheel_voting.wheel_event_key(key, item)
        if notifications._processed(success_records.get(event_key)) or notifications._processed(
            failure_records.get(event_key)
        ):
            continue

        all_success = all(value[2] for value in accounts.values())
        pending_at = _latest_pending_at(accounts)
        account_payload = {
            account_key: {
                "status": str(record.get("status") or ""),
                "success": bool(success),
                "label": notifications._account_identity(record)[1],
            }
            for account_key, (_token, record, success) in accounts.items()
        }
        payload = {
            "wheel_key": key,
            "source_event_token": base_token,
            "completed_at": current.isoformat(),
            "notified_at": "",
            "notification_sent": False,
            "notification_policy": "stale_backlog_suppressed",
            "stale_backlog": True,
            "stale_pending_at": pending_at.isoformat() if pending_at is not None else "",
            "maximum_notification_delay_seconds": int(
                MAX_USER_NOTIFICATION_DELAY.total_seconds()
            ),
            "referral_restricted": notifications.wheel_publications_v2.entry_is_referral_restricted(
                item
            ),
            "accounts": account_payload,
            "original_button_updated": False,
            "vote_changed": False,
            "recovered_event_context": not active_matches,
            "vote_command_id": "",
        }
        if all_success:
            success_records[event_key] = payload
        else:
            failure_records[event_key] = payload
        closed += 1

    if not closed:
        return 0

    limit = int(getattr(owner_sync, "MAX_COMPLETED_EVENTS", 500) or 500)
    owner["auto_participation_success_events"] = _trim(success_records, limit)
    owner["auto_participation_failure_events"] = _trim(failure_records, limit)
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    users[owner_id] = owner
    access["users"] = users
    panel.access = access
    panel.access_loaded = True
    panel.save_access("Suppress stale automatic participation backlog [skip ci]")
    return closed


def install() -> None:
    owner_sync = notifications.auto_participation_owner_sync
    if getattr(owner_sync, "_bbvg_stale_backlog_guard_installed", False):
        return
    original_sync = owner_sync.sync_once

    def sync_once(panel: Any) -> dict[str, int]:
        stale_closed = close_stale_backlog(panel)
        result = original_sync(panel)
        normalized = dict(result) if isinstance(result, dict) else {}
        normalized["stale_backlog_closed"] = stale_closed
        return normalized

    owner_sync.sync_once = sync_once
    owner_sync._bbvg_stale_backlog_guard_installed = True


def self_test() -> None:
    base = "wheel#action:42:2026-07-22T12:00:00+00:00"
    old = {
        notifications.PRIMARY_ACCOUNT_KEY: (
            base,
            {
                "wheel_key": "wheel",
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:00+00:00",
            },
            True,
        ),
        notifications.SECONDARY_ACCOUNT_KEY: (
            base + "#account:vyacheslav_secondary",
            {
                "wheel_key": "wheel",
                "account_key": notifications.SECONDARY_ACCOUNT_KEY,
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:10+00:00",
            },
            True,
        ),
    }
    assert _is_stale(old, now=datetime(2026, 7, 22, 12, 30, tzinfo=UTC))
    assert not _is_stale(old, now=datetime(2026, 7, 22, 12, 10, tzinfo=UTC))
    print("automatic participation stale backlog guard self-test passed")


if __name__ == "__main__":
    self_test()
