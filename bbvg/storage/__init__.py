"""Durable BB V.G. runtime storage.

The storage package owns canonical event identity, the append-only event
ledger, transactional outboxes, account results and safe audit exports.
"""

from .event_store import (
    EventStore,
    canonical_event_id,
    canonical_generation_id,
    canonical_start_at,
    event_id_from_entry,
    legacy_event_aliases,
    status_confidence,
)

__all__ = [
    "EventStore",
    "canonical_event_id",
    "canonical_generation_id",
    "canonical_start_at",
    "event_id_from_entry",
    "legacy_event_aliases",
    "status_confidence",
]
