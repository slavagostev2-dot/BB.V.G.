from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def materialize_event_payload(
    state: dict[str, Any],
    raw_payload: str,
    *,
    received_at: datetime,
) -> str:
    value = json.loads(str(raw_payload))
    if not isinstance(value, dict):
        raise ValueError("BBVG_EVENT_PAYLOAD_JSON must contain an object")
    event_id = str(value.get("event_id") or "").strip()
    wheel_key = str(
        value.get("wheel_key") or value.get("identifier") or ""
    ).strip().casefold()
    if not event_id or not wheel_key:
        raise ValueError("dispatched event_id and wheel_key are required")
    entry = {
        "wheel_key": wheel_key,
        "identifier": str(value.get("identifier") or wheel_key),
        "canonical_event_id": event_id,
        "generation_id": str(value.get("generation_id") or ""),
        "url": str(value.get("url") or ""),
        "source": str(value.get("source") or ""),
        "message_id": value.get("source_message_id"),
        "message_date": value.get("source_message_date"),
        "message_url": str(value.get("source_message_url") or ""),
        "message_text": str(value.get("message_text") or ""),
        "action_id": value.get("action_id"),
        "server_start_at": value.get("server_start_at"),
        "deadline": value.get("deadline"),
        "available_at": value.get("available_at"),
        "expires_at": value.get("expires_at"),
        "verification_status": str(value.get("verification_status") or ""),
        "status": str(value.get("status") or "active"),
        "referral_restricted": bool(value.get("referral_restricted")),
        "durable_dispatch_received_at": received_at.isoformat(),
    }
    state.setdefault("active_wheels", {})[wheel_key] = {
        key: item for key, item in entry.items() if item not in (None, "")
    }
    state.setdefault(
        "auto_participation_event_mode_initialized_at",
        received_at.isoformat(),
    )
    return event_id
