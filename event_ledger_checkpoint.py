from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bbvg.storage import EventStore, event_id_from_entry, legacy_event_aliases
from bbvg.storage.github_sync import sync_from_environment

UTC = timezone.utc
STATE_PATH = Path(__file__).with_name("state.json")


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _entry_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "wheel_key": payload.get("wheel_key"),
        "identifier": payload.get("identifier"),
        "url": payload.get("url"),
        "source": payload.get("source"),
        "message_id": payload.get("source_message_id"),
        "message_date": payload.get("source_message_date"),
        "message_url": payload.get("source_message_url"),
        "message_text": payload.get("message_text"),
        "action_id": payload.get("action_id"),
        "server_start_at": payload.get("server_start_at"),
        "deadline": payload.get("deadline"),
        "available_at": payload.get("available_at"),
        "expires_at": payload.get("expires_at"),
        "verification_status": payload.get("verification_status"),
        "status": payload.get("status") or "active",
        "referral_restricted": payload.get("referral_restricted"),
    }


def checkpoint_state(
    store: EventStore,
    state: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    entry = _entry_from_payload(payload)
    wheel_key = str(entry.get("wheel_key") or "").casefold()
    active = state.get("active_wheels")
    if isinstance(active, dict) and isinstance(active.get(wheel_key), dict):
        entry.update(active[wheel_key])
    expected = str(payload.get("event_id") or event_id_from_entry(entry))
    event_id = store.prepare_event(
        entry,
        enqueue_participation=False,
        enqueue_notification=False,
        discovery_reason="workflow_payload",
    )
    if expected and expected != event_id:
        raise ValueError(
            f"workflow event identity mismatch: expected={expected} actual={event_id}"
        )
    store.record_transition(
        event_id,
        "workflow_started",
        payload={
            "workflow_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "workflow_sha": os.getenv("GITHUB_SHA", ""),
        },
        dedupe_key=os.getenv("GITHUB_RUN_ID", "") or "local",
    )
    identities = {event_id, *legacy_event_aliases(entry, wheel_key=wheel_key)}
    imported = 0
    events = state.get("auto_participation_events")
    if isinstance(events, dict):
        for raw_token, raw in events.items():
            if not isinstance(raw, dict):
                continue
            base = str(raw_token).split("#account:", 1)[0]
            explicit = str(raw.get("event_token") or "").split("#account:", 1)[0]
            if not identities.intersection({base, explicit}):
                continue
            account_key = str(raw.get("account_key") or "").strip()
            if not account_key and "#account:" in str(raw_token):
                account_key = str(raw_token).split("#account:", 1)[1].strip()
            account_key = account_key or "legacy_primary"
            owner_id = str(
                raw.get("account_owner")
                or raw.get("owner_id")
                or "legacy_unknown_owner"
            ).strip()
            account_label = str(
                raw.get("account_label")
                or raw.get("display_name")
                or account_key
            ).strip()
            status = str(raw.get("status") or "unknown").strip()
            try:
                attempts = max(1, int(raw.get("attempt_count", 1) or 1))
            except (TypeError, ValueError):
                attempts = 1
            store.record_account_result(
                event_id,
                owner_id=owner_id,
                account_key=account_key,
                account_label=account_label,
                status=status,
                confirmation=str(
                    raw.get("confirmation")
                    or raw.get("confirmation_method")
                    or "workflow_state"
                ),
                started_at=raw.get("started_at") or raw.get("attempted_at"),
                finished_at=(
                    raw.get("finished_at")
                    or raw.get("attempted_at")
                    or raw.get("recorded_at")
                    or datetime.now(UTC)
                ),
                error_text=str(raw.get("error_text") or raw.get("detail") or ""),
                attempt_count=attempts,
                artifact_url=str(raw.get("artifact_url") or ""),
            )
            imported += 1
    return {"event_id": event_id, "account_results_checkpointed": imported}


def main() -> int:
    raw = os.getenv("BBVG_EVENT_PAYLOAD_JSON", "").strip()
    if not raw:
        print(json.dumps({"status": "no_explicit_event"}, sort_keys=True))
        return 0
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("BBVG_EVENT_PAYLOAD_JSON must contain an object")
    store = EventStore()
    result = checkpoint_state(store, _load_state(), payload)
    sync = sync_from_environment(store)
    print(json.dumps({**result, "sync": sync}, ensure_ascii=False, sort_keys=True))
    return 1 if int(sync.get("retry", 0) or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
