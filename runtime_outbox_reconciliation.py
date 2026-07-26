from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import monitor_data as data_store
from bbvg.storage import EventStore, event_id_from_entry

UTC = timezone.utc
SUPPRESSION_REASON = "monitor_state_not_current_active"
_RECONCILABLE_STATUSES = {"pending", "retry", "claimed", "suppressed"}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _active_event_ids(state: dict[str, Any], store: EventStore) -> set[str]:
    active = state.get("active_wheels")
    if not isinstance(active, dict):
        return set()
    result: set[str] = set()
    for raw_key, raw in active.items():
        if not isinstance(raw, dict):
            continue
        event_id = event_id_from_entry(raw, wheel_key=str(raw_key).casefold())
        if event_id:
            result.add(store.resolve_event_id(event_id))
    return result


def reconcile_runtime_outbox(
    state_path: Path,
    *,
    store: EventStore | None = None,
) -> dict[str, int]:
    """Match the durable auto-participation queue to current Monitor lifecycle.

    A pending/retry row without a matching current ``active_wheels`` generation is
    terminal and must not keep health red or dispatch after the wheel closes. Rows
    suppressed by this exact reconciliation are reopened when the same canonical
    generation becomes active again, so a temporary state gap remains recoverable.
    """

    event_store = store or EventStore()
    active_ids = _active_event_ids(_load_object(state_path), event_store)
    current = datetime.now(UTC).isoformat()
    suppressed = 0
    reopened = 0

    with event_store.transaction() as db:
        rows = db.execute(
            """
            SELECT outbox_id, event_id, status, last_error
            FROM outbox
            WHERE kind='auto_participation'
              AND status IN ('pending', 'retry', 'claimed', 'suppressed')
            """
        ).fetchall()
        for row in rows:
            event_id = str(row["event_id"] or "")
            status = str(row["status"] or "")
            last_error = str(row["last_error"] or "")
            if event_id in active_ids:
                if status == "suppressed" and last_error == SUPPRESSION_REASON:
                    db.execute(
                        """
                        UPDATE outbox
                        SET status='pending', available_at=?, completed_at=NULL,
                            last_error='', claim_token=NULL, claimed_at=NULL,
                            claim_expires_at=NULL, updated_at=?
                        WHERE outbox_id=?
                        """,
                        (current, current, str(row["outbox_id"])),
                    )
                    reopened += 1
                continue
            if status not in {"pending", "retry", "claimed"}:
                continue
            db.execute(
                """
                UPDATE outbox
                SET status='suppressed', last_error=?, completed_at=?,
                    claim_token=NULL, claimed_at=NULL, claim_expires_at=NULL,
                    updated_at=?
                WHERE outbox_id=?
                """,
                (
                    SUPPRESSION_REASON,
                    current,
                    current,
                    str(row["outbox_id"]),
                ),
            )
            suppressed += 1

    return {
        "active_events": len(active_ids),
        "suppressed": suppressed,
        "reopened": reopened,
        "changed": suppressed + reopened,
    }


def refresh_functional_health(
    health_path: Path,
    *,
    store: EventStore | None = None,
) -> bool:
    """Refresh only the durable functional-health block after queue repair."""

    if not health_path.is_file():
        return False
    event_store = store or EventStore()
    health = _load_object(health_path)
    functional = event_store.health()
    if health.get("functional") == functional:
        return False
    health["functional"] = functional
    data_store.atomic_write_json(health_path, health)
    return True
