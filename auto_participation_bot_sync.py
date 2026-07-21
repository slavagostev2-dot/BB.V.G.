from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import monitor

UTC = timezone.utc
DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


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

        if bool(attempt.get("success")):
            # A previous run already confirmed this exact event. Do not generate a
            # retroactive success notification, but clear any stale failure candidate.
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

        # Recovery itself may already have created these fields. Reassert them here
        # from the exact result file so a future refactor cannot restore direct sends.
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
    assert _event_token(success) == "lent#action:952:2026-07-21T14:01:28.861000+00:00"
    assert _event_token(failure) == "ctom11#action:958:2026-07-21T15:28:57.035000+00:00"
    assert _event_token({"wheel_key": "x", "message_date": "now"}) == "x#seen:now"
    print("auto participation bot outcome sync self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-result", default=str(DEFAULT_RECOVERY_RESULT))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    result = queue_recovery_outcomes(Path(args.recovery_result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
