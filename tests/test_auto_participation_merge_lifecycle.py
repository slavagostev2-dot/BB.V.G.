from __future__ import annotations

from datetime import datetime, timedelta, timezone

import auto_participation_bot_sync
import auto_participation_recovery

UTC = timezone.utc


def _event(
    key: str,
    *,
    action_id: int,
    server_start_at: datetime,
    deadline: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "wheel_key": key,
        "identifier": key,
        "url": f"https://betboom.ru/freestream/{key}",
        "action_id": action_id,
        "server_start_at": server_start_at.isoformat(),
        "participating": True,
        "auto_participation_status": "participated",
    }
    if deadline is not None:
        item["deadline"] = deadline.isoformat()
    if expires_at is not None:
        item["expires_at"] = expires_at.isoformat()
    return item


def test_delayed_worker_keeps_outcome_but_does_not_resurrect_expired_wheel() -> None:
    current = datetime(2026, 7, 26, 16, 45, tzinfo=UTC)
    stale = _event(
        "over",
        action_id=1052,
        server_start_at=datetime(2026, 7, 26, 11, 55, tzinfo=UTC),
        deadline=datetime(2026, 7, 26, 12, 15, tzinfo=UTC),
        expires_at=current + timedelta(hours=2),
    )
    local = {
        "active_wheels": {"over": stale},
        "auto_participation_events": {
            "evt:over": {
                "status": "participated",
                "attempted_at": "2026-07-26T12:09:00+00:00",
            }
        },
    }

    merged = auto_participation_bot_sync.merge_auto_participation_state(
        {"active_wheels": {}},
        local,
        current=current,
    )

    assert "over" not in merged.get("active_wheels", {})
    assert merged["auto_participation_events"]["evt:over"]["status"] == "participated"


def test_worker_can_restore_missing_current_wheel_with_future_deadline() -> None:
    current = datetime(2026, 7, 26, 16, 45, tzinfo=UTC)
    active = _event(
        "current",
        action_id=1060,
        server_start_at=current - timedelta(minutes=10),
        deadline=current + timedelta(minutes=20),
        expires_at=current + timedelta(hours=2),
    )

    merged = auto_participation_bot_sync.merge_auto_participation_state(
        {"active_wheels": {}},
        {"active_wheels": {"current": active}},
        current=current,
    )

    assert merged["active_wheels"]["current"]["action_id"] == 1060
    assert merged["active_wheels"]["current"]["participating"] is True


def test_worker_can_restore_recent_untimed_wheel_but_not_old_one() -> None:
    current = datetime(2026, 7, 26, 16, 45, tzinfo=UTC)
    recent = _event(
        "recent",
        action_id=1061,
        server_start_at=current - timedelta(minutes=30),
        expires_at=current + timedelta(hours=1),
    )
    old = _event(
        "old",
        action_id=900,
        server_start_at=current - timedelta(hours=5),
        expires_at=current + timedelta(hours=1),
    )

    merged = auto_participation_bot_sync.merge_auto_participation_state(
        {"active_wheels": {}},
        {"active_wheels": {"recent": recent, "old": old}},
        current=current,
    )

    assert "recent" in merged["active_wheels"]
    assert "old" not in merged["active_wheels"]


def test_expired_newer_generation_cannot_replace_monitor_generation() -> None:
    current = datetime(2026, 7, 26, 16, 45, tzinfo=UTC)
    remote = _event(
        "same-link",
        action_id=200,
        server_start_at=current - timedelta(minutes=20),
        deadline=current + timedelta(minutes=20),
    )
    local = _event(
        "same-link",
        action_id=201,
        server_start_at=current - timedelta(hours=3),
        deadline=current - timedelta(hours=2),
        expires_at=current + timedelta(hours=2),
    )

    merged = auto_participation_bot_sync.merge_auto_participation_state(
        {"active_wheels": {"same-link": remote}},
        {"active_wheels": {"same-link": local}},
        current=current,
    )

    assert merged["active_wheels"]["same-link"]["action_id"] == 200


def test_primary_recovery_does_not_trust_secondary_account_success() -> None:
    started = datetime(2026, 8, 19, 9, 15, tzinfo=UTC)
    item = _event(
        "zonertg8",
        action_id=1290,
        server_start_at=started,
        deadline=started + timedelta(hours=10),
    )
    token = auto_participation_recovery._event_token(item)
    state = {
        "active_wheels": {"zonertg8": item},
        "auto_participation_events": {
            token: {
                "wheel_key": "zonertg8",
                "account_key": "vyacheslav_primary",
                "event_token": token,
                "status": "button_not_found",
            },
            f"{token}#account:vyacheslav_secondary": {
                "wheel_key": "zonertg8",
                "account_key": "vyacheslav_secondary",
                "event_token": token,
                "status": "participated",
            },
        },
    }

    assert auto_participation_recovery._confirmed_success_for_event(state, item) is False


def test_primary_recovery_accepts_exact_primary_success() -> None:
    started = datetime(2026, 8, 19, 9, 15, tzinfo=UTC)
    item = _event(
        "zonertg8",
        action_id=1290,
        server_start_at=started,
        deadline=started + timedelta(hours=10),
    )
    token = auto_participation_recovery._event_token(item)
    state = {
        "auto_participation_events": {
            token: {
                "wheel_key": "zonertg8",
                "account_key": "vyacheslav_primary",
                "event_token": token,
                "status": "participated",
            }
        }
    }

    assert auto_participation_recovery._confirmed_success_for_event(state, item) is True
