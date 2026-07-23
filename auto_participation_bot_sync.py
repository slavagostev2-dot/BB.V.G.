from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import monitor
import wheel_publications_v2

UTC = timezone.utc
DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")

_AUTO_PARTICIPATION_FIELDS = {
    "participating",
    "auto_participation_status",
    "auto_participation_checked_at",
    "auto_participation_confirmed_at",
    "auto_participation_retry_allowed",
    "auto_participation_error",
    "auto_participation_manual_notification_at",
    "auto_participation_manual_notification_error",
    "auto_participation_rearmed_at",
    "auto_participation_rearm_reason",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_recovery_result(path: Path) -> dict[str, Any]:
    """Read the last JSON object emitted by auto_participation_recovery.py."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        value = line.strip()
        if not value:
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _event_token(item: dict[str, Any]) -> str:
    key = str(item.get("wheel_key") or "").casefold()
    try:
        action_id = int(item.get("action_id") or 0)
    except (TypeError, ValueError):
        action_id = 0
    start = str(item.get("server_start_at") or "")
    if action_id > 0:
        return f"{key}#action:{action_id}:{start}"
    return f"{key}#seen:{item.get('message_date') or ''}"


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _record_timestamp(record: dict[str, Any]) -> datetime:
    candidates: list[datetime] = []
    for field, value in record.items():
        if field.endswith("_at") or field in {"attempted_at", "recorded_at"}:
            parsed = _parse_datetime(value)
            if parsed is not None:
                candidates.append(parsed)
    return max(candidates, default=datetime.min.replace(tzinfo=UTC))


def _active_event_marker(record: Any) -> datetime:
    if not isinstance(record, dict):
        return datetime.min.replace(tzinfo=UTC)
    for field in ("server_start_at", "message_date", "first_notified_at", "created_at"):
        parsed = _parse_datetime(record.get(field))
        if parsed is not None:
            return parsed
    return _record_timestamp(record)


def _active_event_is_newer(remote: Any, local: Any) -> bool:
    if not isinstance(local, dict):
        return False
    if not isinstance(remote, dict):
        return True
    remote_token = _event_token(remote)
    local_token = _event_token(local)
    if remote_token == local_token:
        return False
    remote_marker = _active_event_marker(remote)
    local_marker = _active_event_marker(local)
    if local_marker != remote_marker:
        return local_marker > remote_marker
    return _record_timestamp(local) > _record_timestamp(remote)


def _suppress_delivered_recovery_pending(record: Any) -> bool:
    """Do not resurrect a recovery notification after it was already delivered."""

    if not isinstance(record, dict):
        return False
    pending = _parse_datetime(record.get("recovered_initial_notification_pending_at"))
    if pending is None:
        return False
    delivered = [
        value
        for field in (
            "recovered_initial_notification_sent_at",
            "last_notification_at",
            "first_notified_at",
        )
        if (value := _parse_datetime(record.get(field))) is not None
        and value >= pending
    ]
    if not delivered:
        return False
    record.pop("recovered_initial_notification_pending_at", None)
    record.pop("recovered_initial_notification_reason", None)
    record.pop("recovered_initial_notification_error", None)
    record.setdefault(
        "recovered_initial_notification_sent_at",
        max(delivered).isoformat(),
    )
    record["recovered_initial_notification_duplicate_suppressed"] = True
    return True


def _event_context(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
    source = dict(item)
    active = state.get("active_wheels")
    active_item = active.get(key) if isinstance(active, dict) else None
    if isinstance(active_item, dict) and _event_token(active_item) == _event_token(item):
        source = dict(active_item)
        source.update(item)
    fields = (
        "identifier",
        "url",
        "source",
        "message_id",
        "message_date",
        "message_url",
        "message_text",
        "button_token",
        "action_id",
        "server_start_at",
        "deadline",
        "available_at",
        "generation_id",
        "event_id",
    )
    context = {
        field: copy.deepcopy(source[field])
        for field in fields
        if field in source
    }
    context["wheel_key"] = key
    context.setdefault("identifier", key)
    context["referral_restricted"] = (
        wheel_publications_v2.entry_is_referral_restricted(source)
    )
    return context


def _merge_timed_record(remote: Any, local: Any) -> Any:
    if not isinstance(remote, dict):
        return copy.deepcopy(local)
    if not isinstance(local, dict):
        return copy.deepcopy(remote)
    local_is_newer = _record_timestamp(local) >= _record_timestamp(remote)
    older, newer = (remote, local) if local_is_newer else (local, remote)
    result = copy.deepcopy(older)
    result.update(copy.deepcopy(newer))
    return result


def _merge_record_collection(
    target: dict[str, Any],
    remote: Any,
    local: Any,
) -> None:
    remote_rows = remote if isinstance(remote, dict) else {}
    local_rows = local if isinstance(local, dict) else {}
    for key in set(remote_rows) | set(local_rows):
        if key in remote_rows and key in local_rows:
            target[str(key)] = _merge_timed_record(remote_rows[key], local_rows[key])
        elif key in local_rows:
            target[str(key)] = copy.deepcopy(local_rows[key])
        else:
            target[str(key)] = copy.deepcopy(remote_rows[key])


def merge_auto_participation_state(
    remote_state: dict[str, Any],
    local_state: dict[str, Any],
) -> dict[str, Any]:
    """Merge one workflow outcome into the latest monitor state.

    The monitor remains authoritative for lifecycle and source discovery. The isolated
    workflow owns only auto-participation outcome fields and its event/dispatch ledgers.
    This prevents a heartbeat or monitor commit from erasing a confirmed BetBoom result.
    """

    remote = remote_state if isinstance(remote_state, dict) else {}
    local = local_state if isinstance(local_state, dict) else {}
    merged = copy.deepcopy(remote)

    for collection_name in (
        "auto_participation_events",
        "auto_participation_dispatch_events",
        "auto_participation_attempts",
    ):
        rows: dict[str, Any] = {}
        _merge_record_collection(
            rows,
            remote.get(collection_name),
            local.get(collection_name),
        )
        if rows:
            merged[collection_name] = rows

    remote_active = remote.get("active_wheels")
    local_active = local.get("active_wheels")
    active = copy.deepcopy(remote_active) if isinstance(remote_active, dict) else {}
    if isinstance(local_active, dict):
        for raw_key, raw_item in local_active.items():
            key = str(raw_key).casefold()
            if not isinstance(raw_item, dict):
                continue
            current = active.get(key)
            if not isinstance(current, dict):
                active[key] = copy.deepcopy(raw_item)
                continue
            if _active_event_is_newer(current, raw_item):
                updated = copy.deepcopy(current)
                updated.update(copy.deepcopy(raw_item))
                active[key] = updated
                continue
            updated = copy.deepcopy(current)
            for field in _AUTO_PARTICIPATION_FIELDS:
                if field in raw_item:
                    updated[field] = copy.deepcopy(raw_item[field])
            if bool(raw_item.get("participating")):
                updated["participating"] = True
            active[key] = updated
    for item in active.values():
        _suppress_delivered_recovery_pending(item)
    if active:
        merged["active_wheels"] = active

    for collection_name in ("button_contexts", "participating_wheels"):
        remote_rows = remote.get(collection_name)
        local_rows = local.get(collection_name)
        rows = copy.deepcopy(remote_rows) if isinstance(remote_rows, dict) else {}
        if isinstance(local_rows, dict):
            for key, value in local_rows.items():
                normalized = str(key)
                if normalized not in rows:
                    rows[normalized] = copy.deepcopy(value)
                elif collection_name == "participating_wheels":
                    rows[normalized] = _merge_timed_record(rows[normalized], value)
        if rows:
            merged[collection_name] = rows

    remote_publications = remote.get("wheel_publications")
    local_publications = local.get("wheel_publications")
    publications = (
        copy.deepcopy(remote_publications)
        if isinstance(remote_publications, dict)
        else {}
    )
    if isinstance(local_publications, dict):
        for key, value in local_publications.items():
            if key not in publications:
                publications[key] = copy.deepcopy(value)
            elif isinstance(publications.get(key), dict) and isinstance(value, dict):
                combined = copy.deepcopy(publications[key])
                combined.update(copy.deepcopy(value))
                publications[key] = combined
    if publications:
        merged["wheel_publications"] = publications

    if "auto_participation_event_mode_initialized_at" in local:
        merged.setdefault(
            "auto_participation_event_mode_initialized_at",
            local["auto_participation_event_mode_initialized_at"],
        )
    return merged


def merge_state_files(
    local_path: Path,
    remote_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    local = _load_json(local_path, {})
    remote = _load_json(remote_path, {})
    merged = merge_auto_participation_state(remote, local)
    _write_json(output_path, merged)
    return merged


def queue_recovery_outcomes(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    """Queue recovery outcomes for the single live Control Center.

    Workflow/recovery owns only public state.json. It never writes encrypted user
    state and never sends the final success/failure Telegram outcome. Personal
    marking, rating and final user-facing outcome are serialized by Control Center.
    """

    recovery = _load_recovery_result(recovery_result_path)
    state = _load_json(monitor.STATE_PATH, {})
    events = state.setdefault("auto_participation_events", {})
    attempts = (
        recovery.get("attempts")
        if isinstance(recovery.get("attempts"), list)
        else []
    )
    success_queued: list[str] = []
    failure_queued: list[str] = []
    changed = False
    now_text = datetime.now(UTC).isoformat()

    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        token = _event_token(attempt)
        record = events.get(token)
        if not isinstance(record, dict):
            continue
        context = _event_context(state, attempt)
        if context and record.get("event_context") != context:
            record["event_context"] = context
            changed = True

        if bool(attempt.get("success")):
            for field in (
                "bot_failure_pending_at",
                "bot_failure_sync_status",
                "bot_failure_sync_version",
                "bot_failure_status",
                "bot_failure_detail",
            ):
                if field in record:
                    record.pop(field, None)
                    changed = True

            if str(attempt.get("status") or "") == "already_marked_participating":
                continue
            if str(record.get("status") or "") != "participated":
                continue
            if not record.get("bot_success_pending_at"):
                record["bot_success_pending_at"] = now_text
                record["bot_success_sync_status"] = "waiting_for_control_center"
                record["bot_success_sync_version"] = 1
                success_queued.append(token)
                changed = True
            continue

        if bool(record.get("manual_notification_sent")):
            continue
        if record.get("bot_success_pending_at"):
            continue
        if str(record.get("status") or "") in {
            "participated",
            "already_marked_participating",
        }:
            continue
        if not record.get("bot_failure_pending_at"):
            record["bot_failure_pending_at"] = now_text
            failure_queued.append(token)
            changed = True
        record["bot_failure_sync_status"] = "waiting_for_control_center"
        record["bot_failure_sync_version"] = 1
        record["bot_failure_status"] = str(attempt.get("status") or "failed")[:80]
        record["bot_failure_detail"] = str(
            attempt.get("detail") or "автоучастие не подтверждено"
        )[:300]
        changed = True

    if changed:
        monitor.save_state(state)
    return {
        "success_queued": len(success_queued),
        "failure_queued": len(failure_queued),
        "success_events": success_queued,
        "failure_events": failure_queued,
    }


def queue_confirmed_participation(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    """Backward-compatible entrypoint; outcomes are now finalized by Control Center."""

    return queue_recovery_outcomes(recovery_result_path)


def self_test() -> None:
    success = {
        "wheel_key": "lent",
        "action_id": 952,
        "server_start_at": "2026-07-21T14:01:28.861000+00:00",
        "success": True,
        "status": "participated",
    }
    failure = {
        "wheel_key": "ctom11",
        "action_id": 958,
        "server_start_at": "2026-07-21T15:28:57.035000+00:00",
        "success": False,
        "status": "unconfirmed",
    }
    assert _event_token(success) == (
        "lent#action:952:2026-07-21T14:01:28.861000+00:00"
    )
    assert _event_token(failure) == (
        "ctom11#action:958:2026-07-21T15:28:57.035000+00:00"
    )
    assert _event_token({"wheel_key": "x", "message_date": "now"}) == "x#seen:now"

    recurring_remote = {
        "active_wheels": {
            "zonertw5": {
                "wheel_key": "zonertw5",
                "action_id": 961,
                "server_start_at": "2026-07-22T16:27:00+00:00",
                "last_checked_at": "2026-07-22T18:30:00+00:00",
            }
        }
    }
    recurring_local = {
        "active_wheels": {
            "zonertw5": {
                "wheel_key": "zonertw5",
                "action_id": 989,
                "server_start_at": "2026-07-22T18:26:05+00:00",
                "message_date": "2026-07-22T18:27:00+00:00",
                "participating": True,
            }
        }
    }
    recurring_merged = merge_auto_participation_state(
        recurring_remote,
        recurring_local,
    )
    assert recurring_merged["active_wheels"]["zonertw5"]["action_id"] == 989
    assert recurring_merged["active_wheels"]["zonertw5"]["participating"] is True

    delivered_remote = {
        "active_wheels": {
            "zonertg14": {
                "wheel_key": "zonertg14",
                "action_id": 699,
                "server_start_at": "2026-07-23T09:11:39.433000+00:00",
                "first_notified_at": "2026-07-23T13:22:09.380714+00:00",
                "last_notification_at": "2026-07-23T14:28:42.904375+00:00",
            }
        }
    }
    stale_local = {
        "active_wheels": {
            "zonertg14": {
                "wheel_key": "zonertg14",
                "action_id": 699,
                "server_start_at": "2026-07-23T09:11:39.433000+00:00",
                "recovered_initial_notification_pending_at": (
                    "2026-07-23T11:59:16.844122+00:00"
                ),
                "recovered_initial_notification_reason": (
                    "recovery_discovered_missing_event"
                ),
                "auto_participation_checked_at": (
                    "2026-07-23T14:49:27.088931+00:00"
                ),
            }
        }
    }
    delivered_merged = merge_auto_participation_state(
        delivered_remote,
        stale_local,
    )
    delivered_item = delivered_merged["active_wheels"]["zonertg14"]
    assert "recovered_initial_notification_pending_at" not in delivered_item
    assert delivered_item["recovered_initial_notification_duplicate_suppressed"] is True

    remote = {
        "version": 6,
        "active_wheels": {
            "wheel": {
                "wheel_key": "wheel",
                "last_checked_at": "new-monitor-value",
                "participating": False,
            }
        },
        "auto_participation_events": {
            "wheel#action:1:start": {
                "wheel_key": "wheel",
                "status": "queued",
                "recorded_at": "2026-07-22T08:00:00+00:00",
                "remote_field": True,
            }
        },
    }
    local = {
        "active_wheels": {
            "wheel": {
                "wheel_key": "wheel",
                "last_checked_at": "stale-worker-value",
                "participating": True,
                "auto_participation_status": "participated",
            }
        },
        "auto_participation_events": {
            "wheel#action:1:start": {
                "wheel_key": "wheel",
                "status": "participated",
                "attempted_at": "2026-07-22T08:01:00+00:00",
                "bot_success_pending_at": "2026-07-22T08:01:01+00:00",
            }
        },
    }
    merged = merge_auto_participation_state(remote, local)
    item = merged["active_wheels"]["wheel"]
    assert item["last_checked_at"] == "new-monitor-value"
    assert item["participating"] is True
    assert item["auto_participation_status"] == "participated"
    event = merged["auto_participation_events"]["wheel#action:1:start"]
    assert event["status"] == "participated"
    assert event["remote_field"] is True
    assert event["bot_success_pending_at"]
    print("auto participation bot outcome sync self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-result", default=str(DEFAULT_RECOVERY_RESULT))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--merge-local", type=Path)
    parser.add_argument("--merge-remote", type=Path)
    parser.add_argument("--merge-output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    merge_args = (args.merge_local, args.merge_remote, args.merge_output)
    if any(merge_args):
        if not all(merge_args):
            parser.error(
                "--merge-local, --merge-remote and --merge-output are required together"
            )
        merge_state_files(args.merge_local, args.merge_remote, args.merge_output)
        print(
            json.dumps(
                {"merged": True, "output": str(args.merge_output)},
                ensure_ascii=False,
            )
        )
        return 0
    result = queue_recovery_outcomes(Path(args.recovery_result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
