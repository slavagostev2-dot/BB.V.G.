from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bbvg.reconciliation import reconcile_candidates, report_day
from bbvg.storage import EventStore

UTC = timezone.utc


def _store(tmp_path: Path) -> EventStore:
    return EventStore(
        tmp_path / "events.sqlite3",
        audit_path=tmp_path / "events.jsonl",
        notification_audit_path=tmp_path / "notifications.jsonl",
    )


def test_reconciliation_recovers_artificially_missed_active_event(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = {
        "wheel_key": "missed-wheel",
        "identifier": "missed-wheel",
        "url": "https://betboom.ru/freestream/missed-wheel",
        "source": "collector",
        "message_id": 77,
        "message_date": "2026-07-25T12:00:00+00:00",
        "message_url": "https://t.me/collector/77",
        "action_id": 9001,
        "server_start_at": "2026-07-25T12:00:05+00:00",
        "deadline": "2026-07-25T13:00:00+00:00",
        "status": "active",
        "verification_status": "confirmed",
    }

    summary = reconcile_candidates(
        store,
        [candidate],
        current=datetime(2026, 7, 25, 12, 5, tzinfo=UTC),
        recovery_reason="test_missed_event",
    )

    assert summary["recovered"] == 1
    assert summary["active_recovered"] == 1
    db = sqlite3.connect(store.path)
    try:
        kinds = {
            row[0]
            for row in db.execute(
                "SELECT kind FROM outbox WHERE status='pending'"
            ).fetchall()
        }
    finally:
        db.close()
    assert {"auto_participation", "new_wheel_notification"} <= kinds
    assert report_day(store, "2026-07-25")[0]["dispatch_queued"] is True


def test_reconciliation_keeps_repeated_wheel_generations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    base = {
        "wheel_key": "repeat",
        "identifier": "repeat",
        "url": "https://betboom.ru/freestream/repeat",
        "source": "collector",
        "message_id": 10,
        "message_date": "2026-07-25T10:00:00+00:00",
        "action_id": 1,
        "server_start_at": "2026-07-25T10:00:05+00:00",
        "status": "inactive",
    }
    second = {
        **base,
        "message_id": 11,
        "message_date": "2026-07-25T14:00:00+00:00",
        "action_id": 2,
        "server_start_at": "2026-07-25T14:00:05+00:00",
    }

    reconcile_candidates(store, [base, second])

    assert len(report_day(store, "2026-07-25")) == 2
