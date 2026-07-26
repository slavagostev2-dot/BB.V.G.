from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bbvg.storage import EventStore
from runtime_outbox_reconciliation import (
    SUPPRESSION_REASON,
    reconcile_runtime_outbox,
    refresh_functional_health,
)


def _store(tmp_path: Path) -> EventStore:
    return EventStore(
        tmp_path / "events.sqlite3",
        audit_path=tmp_path / "events.jsonl",
        notification_audit_path=tmp_path / "notifications.jsonl",
    )


def _event() -> dict[str, object]:
    return {
        "wheel_key": "current-wheel",
        "identifier": "current-wheel",
        "url": "https://betboom.ru/freestream/current-wheel",
        "source": "collector",
        "message_id": 71,
        "message_date": "2026-07-26T16:00:00+00:00",
        "action_id": 1060,
        "server_start_at": "2026-07-26T16:00:05+00:00",
        "verification_status": "confirmed",
        "status": "active",
    }


def _outbox_row(store: EventStore, event_id: str) -> tuple[str, str]:
    db = sqlite3.connect(store.path)
    try:
        row = db.execute(
            """
            SELECT status, COALESCE(last_error, '')
            FROM outbox
            WHERE event_id=? AND kind='auto_participation'
            """,
            (event_id,),
        ).fetchone()
    finally:
        db.close()
    assert row is not None
    return str(row[0]), str(row[1])


def test_terminal_outbox_is_suppressed_and_same_generation_can_reopen(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    event = _event()
    event_id = store.prepare_event(event, enqueue_notification=False)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"active_wheels": {"current-wheel": event}}),
        encoding="utf-8",
    )

    live = reconcile_runtime_outbox(state_path, store=store)
    assert live == {
        "active_events": 1,
        "suppressed": 0,
        "reopened": 0,
        "changed": 0,
    }
    assert _outbox_row(store, event_id)[0] == "pending"

    state_path.write_text(json.dumps({"active_wheels": {}}), encoding="utf-8")
    closed = reconcile_runtime_outbox(state_path, store=store)
    assert closed == {
        "active_events": 0,
        "suppressed": 1,
        "reopened": 0,
        "changed": 1,
    }
    assert _outbox_row(store, event_id) == ("suppressed", SUPPRESSION_REASON)
    assert store.health()["dispatch_health"] == "ok"

    state_path.write_text(
        json.dumps({"active_wheels": {"current-wheel": event}}),
        encoding="utf-8",
    )
    restored = reconcile_runtime_outbox(state_path, store=store)
    assert restored == {
        "active_events": 1,
        "suppressed": 0,
        "reopened": 1,
        "changed": 1,
    }
    assert _outbox_row(store, event_id) == ("pending", "")


def test_functional_health_is_refreshed_after_queue_repair(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event_id = store.prepare_event(_event(), enqueue_notification=False)
    state_path = tmp_path / "state.json"
    health_path = tmp_path / "source_health.json"
    state_path.write_text(json.dumps({"active_wheels": {}}), encoding="utf-8")
    health_path.write_text(
        json.dumps(
            {
                "functional": {"dispatch_health": "backlogged"},
                "sources": {"collector": {"status": "ok"}},
            }
        ),
        encoding="utf-8",
    )

    assert reconcile_runtime_outbox(state_path, store=store)["suppressed"] == 1
    assert _outbox_row(store, event_id)[0] == "suppressed"
    assert refresh_functional_health(health_path, store=store) is True

    health = json.loads(health_path.read_text(encoding="utf-8"))
    assert health["functional"]["dispatch_health"] == "ok"
    assert health["functional"]["pending_by_kind"] == {"github_ledger_sync": 1}
    assert health["sources"]["collector"]["status"] == "ok"
    assert refresh_functional_health(health_path, store=store) is False


def test_runtime_publisher_runs_reconciliation_for_state_and_health() -> None:
    source = Path("runtime_state_publisher.py").read_text(encoding="utf-8")
    assert 'args.publish_monitor_runtime.name in {"state.json", "source_health.json"}' in source
    assert "reconcile_runtime_outbox(state_path)" in source
    assert "refresh_functional_health(" in source
