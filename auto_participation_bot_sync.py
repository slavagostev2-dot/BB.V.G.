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


def queue_confirmed_participation(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    """Queue only fresh confirmed BetBoom successes for the live Control Center.

    The workflow owns public state.json only. It never writes encrypted user state,
    never creates a rating vote and never sends the success Telegram message itself.
    Those personal operations stay inside the single live Control Center process.
    """

    recovery = _load_recovery_result(recovery_result_path)
    state = _load_json(monitor.STATE_PATH, {})
    events = state.setdefault("auto_participation_events", {})
    attempts = recovery.get("attempts") if isinstance(recovery.get("attempts"), list) else []
    queued: list[str] = []
    now_text = datetime.now(UTC).isoformat()

    for attempt in attempts:
        if not isinstance(attempt, dict) or not bool(attempt.get("success")):
            continue
        # This status means a previous run had already completed BetBoom participation;
        # it must not generate a retroactive or duplicate success notification.
        if str(attempt.get("status") or "") == "already_marked_participating":
            continue
        token = _event_token(attempt)
        record = events.get(token)
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or "") != "participated":
            continue
        if record.get("bot_success_pending_at"):
            continue
        record["bot_success_pending_at"] = now_text
        record["bot_success_sync_status"] = "waiting_for_control_center"
        record["bot_success_sync_version"] = 1
        queued.append(token)

    if queued:
        monitor.save_state(state)
    return {"queued": len(queued), "events": queued}


def self_test() -> None:
    attempts = [
        {
            "wheel_key": "lent",
            "action_id": 952,
            "server_start_at": "2026-07-21T14:01:28.861000+00:00",
            "success": True,
            "status": "participated",
        }
    ]
    assert _event_token(attempts[0]) == "lent#action:952:2026-07-21T14:01:28.861000+00:00"
    assert _event_token({"wheel_key": "x", "message_date": "now"}) == "x#seen:now"
    print("auto participation bot sync self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-result", default=str(DEFAULT_RECOVERY_RESULT))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    result = queue_confirmed_participation(Path(args.recovery_result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
