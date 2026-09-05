from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import monitor_data as data_store
import personal_wheel_voting
from bbvg.storage import EventStore, event_id_from_entry

UTC = timezone.utc

_RATING_FIELDS = (
    "personal_vote_points",
    "personal_vote_score",
    "quality_score",
    "personal_votes",
    "user_votes",
    "admin_votes",
    "last_vote_at",
)
_ORIGINAL_LOAD_STATS = data_store.load_stats
_ORIGINAL_SAVE_STATS = data_store.save_stats


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


def reconcile_personal_rating_inventory(data: dict[str, Any]) -> bool:
    """Keep personal rating derived only from valid votes for configured sources.

    Participation votes remain authoritative event history, but synthetic or removed
    sources must not survive inside their rating payload. Operational source counters
    are intentionally untouched.
    """

    if str(data.get("source_rating_policy") or "") != personal_wheel_voting.PERSONAL_RATING_POLICY:
        return False
    allowed = data_store.configured_source_keys()
    if not allowed:
        return False

    votes = data.get("personal_wheel_votes")
    votes = votes if isinstance(votes, dict) else {}
    expected: dict[str, dict[str, Any]] = {}
    changed = False

    for vote_id, raw_vote in votes.items():
        if not isinstance(raw_vote, dict):
            continue
        try:
            payload = personal_wheel_voting.normalize_vote_payload(raw_vote)
        except (TypeError, ValueError):
            continue
        filtered = [
            source for source in payload["sources"]
            if data_store.clean_username(source).casefold() in allowed
        ]
        raw_sources = raw_vote.get("sources")
        if not isinstance(raw_sources, list) or raw_sources != filtered:
            raw_vote["sources"] = filtered
            changed = True

        voted_at = _parse(raw_vote.get("voted_at"))
        for source in filtered:
            folded = data_store.clean_username(source).casefold()
            row = expected.setdefault(
                folded,
                {
                    "source": data_store.clean_username(source),
                    "points": {},
                    "personal_votes": 0,
                    "user_votes": 0,
                    "admin_votes": 0,
                    "last_vote_at": None,
                },
            )
            row["points"][str(vote_id)] = int(payload["weight"])
            row["personal_votes"] += 1
            metric = "admin_votes" if payload["role"] in {"admin", "owner"} else "user_votes"
            row[metric] += 1
            if voted_at is not None and (
                row["last_vote_at"] is None or voted_at > row["last_vote_at"]
            ):
                row["last_vote_at"] = voted_at

    sources = data.setdefault("sources", {})
    grouped: dict[str, list[str]] = {}
    for source, entry in sources.items():
        if isinstance(entry, dict):
            grouped.setdefault(data_store.clean_username(source).casefold(), []).append(str(source))

    def apply_rating(entry: dict[str, Any], desired: dict[str, Any] | None) -> bool:
        local_changed = False
        values: dict[str, Any] = {}
        if desired is not None:
            points = dict(desired["points"])
            score = sum(max(0, int(value or 0)) for value in points.values())
            values = {
                "personal_vote_points": points,
                "personal_vote_score": score,
                "quality_score": score,
                "personal_votes": int(desired["personal_votes"]),
                "user_votes": int(desired["user_votes"]),
                "admin_votes": int(desired["admin_votes"]),
            }
            if desired["last_vote_at"] is not None:
                values["last_vote_at"] = desired["last_vote_at"].isoformat()
        for field in _RATING_FIELDS:
            if field in values:
                if entry.get(field) != values[field]:
                    entry[field] = values[field]
                    local_changed = True
            elif field in entry:
                entry.pop(field, None)
                local_changed = True
        return local_changed

    handled: set[str] = set()
    for folded, keys in grouped.items():
        desired = expected.get(folded)
        preferred = None
        if desired is not None:
            preferred = next(
                (key for key in keys if key == desired["source"]),
                sorted(keys, key=lambda value: (value.casefold(), value))[0],
            )
        for key in keys:
            if apply_rating(sources[key], desired if key == preferred else None):
                changed = True
        handled.add(folded)

    for folded, desired in expected.items():
        if folded in handled:
            continue
        source = str(desired["source"])
        entry = sources.setdefault(source, {})
        if apply_rating(entry, desired):
            changed = True

    return changed


def load_stats_with_personal_rating_reconciliation() -> dict[str, Any]:
    data = _ORIGINAL_LOAD_STATS()
    if reconcile_personal_rating_inventory(data):
        _ORIGINAL_SAVE_STATS(data)
    return data


def save_stats_with_personal_rating_reconciliation(data: dict[str, Any]) -> None:
    reconcile_personal_rating_inventory(data)
    _ORIGINAL_SAVE_STATS(data)


def install_personal_rating_reconciliation() -> None:
    if getattr(data_store, "_bbvg_personal_rating_inventory_installed", False):
        return
    data_store.load_stats = load_stats_with_personal_rating_reconciliation
    data_store.save_stats = save_stats_with_personal_rating_reconciliation
    data_store._bbvg_personal_rating_inventory_installed = True


def state_generation_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return each historical generation and identify only current active rows.

    Historical observations intentionally remain in the candidate list for audit
    repair, but only a row still present in ``active_wheels`` receives the
    ``current_active`` marker and its live expiry/deadline window. This prevents
    an old observation that once had an ``active`` status from being requeued for
    browser participation on every process restart.
    """

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
            observation_metadata = {
                name: item.get(name)
                for name in (
                    "first_seen_at",
                    "last_seen_at",
                    "observations",
                    "statuses",
                )
                if item.get(name) is not None
            }
            if current_active:
                item.update(current_active)
                item.update(observation_metadata)
                item["status"] = "current_active"
            else:
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
            current = dict(item)
            current["status"] = "current_active"
            candidates.append(current)
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
    """Idempotently repair missing canonical events and current dispatch outboxes.

    Every candidate is restored to the durable audit ledger. Automatic browser
    participation and a notification outbox are recreated only when the candidate
    has explicit current-active evidence and a concrete future deadline/expiry.
    """

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
        deadline = _parse(entry.get("deadline"))
        window_end = deadline or _parse(entry.get("expires_at"))
        status = str(entry.get("status") or "").casefold()
        active = (
            status
            in {
                "active",
                "preliminary",
                "available",
                "active_observed",
                "current_active",
            }
            and window_end is not None
            and window_end > now
        )
        stored_id = store.prepare_event(
            entry,
            detected_at=entry.get("first_seen_at"),
            enqueue_participation=active,
            enqueue_notification=active,
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


install_personal_rating_reconciliation()


if __name__ == "__main__":
    raise SystemExit(main())
