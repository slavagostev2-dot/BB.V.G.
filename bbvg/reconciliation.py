from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bbvg.storage import EventStore, event_id_from_entry

UTC = timezone.utc


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def state_generation_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return each historical generation, never only current active wheels."""

    observations = state.get("wheel_generation_observations")
    candidates: list[dict[str, Any]] = []
    active = state.get("active_wheels")
    active = active if isinstance(active, dict) else {}
    active_by_action: dict[tuple[str, int], dict[str, Any]] = {}
    for raw_key, raw in active.items():
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        wheel = str(
            item.get("wheel_key") or item.get("identifier") or raw_key
        ).strip().casefold()
        try:
            action = int(item.get("action_id") or 0)
        except (TypeError, ValueError):
            action = 0
        item["wheel_key"] = wheel
        item.setdefault("identifier", wheel)
        if wheel and action:
            active_by_action[(wheel, action)] = item
    if isinstance(observations, dict):
        for raw in observations.values():
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["identifier"] = item.get("wheel_key")
            wheel = str(item.get("wheel_key") or "").strip().casefold()
            try:
                action = int(item.get("action_id") or 0)
            except (TypeError, ValueError):
                action = 0
            current_active = active_by_action.get((wheel, action))
            if current_active and not _parse(item.get("server_start_at")):
                first_seen = item.get("first_seen_at")
                statuses = item.get("statuses")
                item.update(current_active)
                item["first_seen_at"] = first_seen
                item["statuses"] = statuses
            statuses = item.get("statuses")
            statuses = statuses if isinstance(statuses, dict) else {}
            if int(statuses.get("active", 0) or 0) > 0:
                item["status"] = "active_observed"
            elif int(statuses.get("closed", 0) or 0) > 0:
                item["status"] = "closed_observed"
            else:
                item["status"] = "inactive_only"
            candidates.append(item)
    known = {event_id_from_entry(item) for item in candidates}
    for item in active_by_action.values():
        if event_id_from_entry(item) not in known:
            candidates.append(item)
    return sorted(
        candidates,
        key=lambda item: (
            str(item.get("server_start_at") or item.get("first_seen_at") or ""),
            str(item.get("wheel_key") or ""),
        ),
    )


def _latency_seconds(later: Any, earlier: Any) -> int | None:
    end = _parse(later)
    start = _parse(earlier)
    if end is None or start is None:
        return None
    return int((end - start).total_seconds())


def reconcile_candidates(
    store: EventStore,
    candidates: Iterable[dict[str, Any]],
    *,
    current: datetime | None = None,
    recovery_reason: str = "reconciliation",
) -> dict[str, int]:
    """Idempotently repair missing canonical events and their dispatch outboxes."""

    now = (current or datetime.now(UTC)).astimezone(UTC)
    summary = {
        "candidates": 0,
        "already_present": 0,
        "recovered": 0,
        "active_recovered": 0,
    }
    for raw in candidates:
        entry = dict(raw)
        summary["candidates"] += 1
        event_id = event_id_from_entry(entry)
        if not event_id:
            continue
        try:
            store.event_snapshot(event_id)
        except KeyError:
            exists = False
        else:
            exists = True
        deadline = _parse(entry.get("deadline") or entry.get("expires_at"))
        status = str(entry.get("status") or "").casefold()
        active = (
            status in {"active", "preliminary", "available", "active_observed"}
            and (deadline is None or deadline > now)
        )
        stored_id = store.prepare_event(
            entry,
            detected_at=entry.get("first_seen_at"),
            enqueue_participation=active,
            enqueue_notification=active
            and not bool(entry.get("referral_restricted")),
            discovery_reason=recovery_reason,
        )
        store.record_transition(
            stored_id,
            "reconciled",
            payload={
                "previously_present": exists,
                "active_at_reconciliation": active,
                "recovery_reason": recovery_reason,
            },
            dedupe_key=recovery_reason,
        )
        if exists:
            summary["already_present"] += 1
        else:
            summary["recovered"] += 1
            if active:
                summary["active_recovered"] += 1
    return summary


def report_day(store: EventStore, day: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in store.day_report(day):
        detected_latency = _latency_seconds(
            event.get("discovered_at"),
            event.get("source_message_date") or event.get("server_start_at"),
        )
        dispatch_latency = _latency_seconds(
            event.get("dispatch_queued_at"),
            event.get("discovered_at"),
        )
        workflow_latency = _latency_seconds(
            event.get("workflow_started_at"),
            event.get("dispatch_queued_at"),
        )
        browser_latency = _latency_seconds(
            event.get("browser_started_at"),
            event.get("workflow_started_at"),
        )
        rows.append(
            {
                "event_id": event["event_id"],
                "generation_id": event["generation_id"],
                "wheel_key": event["wheel_key"],
                "action_id": event["action_id"],
                "server_start_at": event["server_start_at"],
                "source": event["source"],
                "source_message_id": event["source_message_id"],
                "discovered_at": event["discovered_at"],
                "status": event["status"],
                "detected_latency_seconds": detected_latency,
                "detection": (
                    "late"
                    if detected_latency is not None and detected_latency > 300
                    else "on_time"
                ),
                "persisted": bool(event["persisted_at"]),
                "dispatch_queued": bool(event["dispatch_queued_at"]),
                "workflow_started": bool(event["workflow_started_at"]),
                "browser_started": bool(event["browser_started_at"]),
                "notification_sent": bool(event["notification_sent_at"]),
                "closed": bool(event["closed_at"]),
                "final_sent": bool(event["final_sent_at"]),
                "dispatch_latency_seconds": dispatch_latency,
                "workflow_latency_seconds": workflow_latency,
                "browser_latency_seconds": browser_latency,
                "account_results": event["account_results"],
                "notifications": event["notifications"],
            }
        )
    return rows


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True)
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args(argv)
    store = EventStore()
    state = _load_state(args.state)
    migration = store.import_legacy_state(state)
    reconciliation: dict[str, int] = {}
    if args.recover:
        reconciliation = reconcile_candidates(
            store,
            state_generation_candidates(state),
            recovery_reason="state_generation_reconciliation",
        )
    payload = {
        "day": args.day,
        "migration": migration,
        "reconciliation": reconciliation,
        "events": report_day(store, args.day),
        "health": store.health(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
