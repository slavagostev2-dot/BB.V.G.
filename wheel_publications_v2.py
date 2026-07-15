from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


UTC = timezone.utc


def _clean_source(value: Any) -> str:
    return str(value or "").strip().lstrip("@")


def _publication_key(row: dict[str, Any]) -> tuple[str, int, str]:
    source = _clean_source(row.get("source")).casefold()
    try:
        message_id = int(row.get("message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    message_url = str(row.get("message_url") or "")
    return source, message_id, message_url


def _normalized_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source = _clean_source(row.get("source"))
    if not source:
        return None
    try:
        message_id = int(row.get("message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    return {
        "source": source,
        "message_id": message_id,
        "message_date": str(row.get("message_date") or row.get("created_at") or ""),
        "message_url": str(row.get("message_url") or ""),
        **(
            {"has_future_deadline": bool(row.get("has_future_deadline"))}
            if "has_future_deadline" in row
            else {}
        ),
    }


def merge_publications(
    existing: Any,
    incoming: Any,
    *,
    reset_event: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not reset_event and isinstance(existing, list):
        rows.extend(row for row in existing if isinstance(row, dict))
    if isinstance(incoming, list):
        rows.extend(row for row in incoming if isinstance(row, dict))

    merged: dict[tuple[str, int, str], dict[str, Any]] = {}
    for raw in rows:
        row = _normalized_row(raw)
        if row is None:
            continue
        key = _publication_key(row)
        previous = merged.get(key)
        if previous is None:
            merged[key] = row
            continue
        if row.get("message_date") and not previous.get("message_date"):
            previous["message_date"] = row["message_date"]
        if row.get("message_url") and not previous.get("message_url"):
            previous["message_url"] = row["message_url"]
        if row.get("has_future_deadline"):
            previous["has_future_deadline"] = True

    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("message_date") or ""),
            str(item.get("source") or "").casefold(),
            int(item.get("message_id", 0) or 0),
        ),
    )


def publication_sources(state: dict[str, Any], key: str, fallback: Any = None) -> list[str]:
    result: list[str] = []
    rows = state.get("wheel_publications", {}).get(str(key).casefold(), [])
    if isinstance(rows, list):
        result.extend(
            _clean_source(row.get("source"))
            for row in rows
            if isinstance(row, dict)
        )
    if isinstance(fallback, dict):
        result.append(_clean_source(fallback.get("source")))
    seen: set[str] = set()
    unique: list[str] = []
    for source in result:
        if source and source.casefold() not in seen:
            seen.add(source.casefold())
            unique.append(source)
    return unique


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def closed_event_blocks_publications(
    state: dict[str, Any],
    key: str,
    incoming: Any,
) -> bool:
    """Return whether publications belong to an already closed wheel event."""

    normalized = str(key or "").casefold()
    if normalized in state.get("active_wheels", {}):
        return False
    inactive = state.get("inactive_wheels", {}).get(normalized)
    if isinstance(inactive, dict):
        return True
    completed = state.get("recently_completed_wheels", {}).get(normalized)
    if not isinstance(completed, dict):
        return False
    closed_at = _parse_datetime(
        completed.get("removed_at") or completed.get("confirmed_finished_at")
    )
    if closed_at is None:
        return True
    rows = incoming if isinstance(incoming, list) else []
    newest = max(
        (
            value
            for row in rows
            if isinstance(row, dict)
            for value in [_parse_datetime(row.get("message_date"))]
            if value is not None
        ),
        default=None,
    )
    return newest is None or newest <= closed_at


def prune_closed_publications(state: dict[str, Any]) -> int:
    publications = state.get("wheel_publications")
    if not isinstance(publications, dict):
        return 0
    removed = 0
    for raw_key in list(publications):
        key = str(raw_key).casefold()
        rows = publications.get(raw_key)
        if closed_event_blocks_publications(state, key, rows):
            publications.pop(raw_key, None)
            removed += 1
    return removed


def install(monitor_module: Any, runtime_module: Any) -> None:
    """Persist every Telegram publication for one current wheel event.

    The monitor keeps a single canonical post for notification and deadline
    extraction. This layer retains all other publications so the active list and
    source rating can credit every channel that found the same wheel. Duplicate
    alert checks also persist the newly found source before suppressing a second
    notification.
    """

    base_runtime = runtime_module.base_runtime
    if getattr(base_runtime, "_bbvg_publication_merge_v2_installed", False):
        return

    original: Callable = base_runtime._persist_publications
    original_suppressed: Callable = monitor_module.is_suppressed
    original_activation_suppressed: Callable = monitor_module.is_activation_suppressed
    original_load_state: Callable = monitor_module.load_state

    def load_state_without_closed_publications() -> dict[str, Any]:
        state = original_load_state()
        prune_closed_publications(state)
        return state

    def persist_merged(state: dict, key: str, fallback: dict | None = None) -> None:
        normalized = str(key or "").casefold()
        collection = state.setdefault("wheel_publications", {})
        previous = collection.get(normalized, [])
        reset_event = normalized not in state.setdefault("active_wheels", {})

        incoming_rows = base_runtime._WHEEL_PUBLICATIONS.get(normalized, [])
        if closed_event_blocks_publications(state, normalized, incoming_rows):
            collection.pop(normalized, None)
            return

        original(state, normalized, fallback)
        incoming = collection.get(normalized, [])
        merged = merge_publications(previous, incoming, reset_event=reset_event)
        if merged:
            collection[normalized] = merged
        else:
            collection.pop(normalized, None)

        active = state.get("active_wheels", {}).get(normalized)
        if isinstance(active, dict):
            active["sources"] = publication_sources(state, normalized, active)

    def persist_before_suppression(state: dict, link: str) -> None:
        key = monitor_module.wheel_key(link)
        fallback = state.get("active_wheels", {}).get(key)
        persist_merged(state, key, fallback if isinstance(fallback, dict) else None)

    def is_suppressed_with_publications(state: dict, link: str) -> bool:
        persist_before_suppression(state, link)
        return bool(original_suppressed(state, link))

    def is_activation_suppressed_with_publications(state: dict, link: str) -> bool:
        persist_before_suppression(state, link)
        return bool(original_activation_suppressed(state, link))

    base_runtime._persist_publications = persist_merged
    monitor_module.load_state = load_state_without_closed_publications
    monitor_module.is_suppressed = is_suppressed_with_publications
    monitor_module.is_activation_suppressed = is_activation_suppressed_with_publications
    base_runtime._bbvg_publication_merge_v2_installed = True
    monitor_module._bbvg_publication_merge_v2_installed = True


def self_test() -> None:
    first = [
        {
            "source": "official",
            "message_id": 10,
            "message_date": "2026-07-14T10:00:00+00:00",
            "message_url": "https://telegram.me/official/10",
        }
    ]
    second = [
        {
            "source": "collector",
            "message_id": 20,
            "message_date": "2026-07-14T11:00:00+00:00",
            "message_url": "https://telegram.me/collector/20",
        },
        dict(first[0]),
    ]
    merged = merge_publications(first, second)
    assert [row["source"] for row in merged] == ["official", "collector"]
    assert merge_publications(first, second, reset_event=True)[0]["source"] == "official"
    state = {"wheel_publications": {"wheel": merged}}
    assert publication_sources(state, "wheel") == ["official", "collector"]

    closed_state = {
        "active_wheels": {},
        "inactive_wheels": {},
        "recently_completed_wheels": {
            "wheel": {"removed_at": "2026-07-14T12:00:00+00:00"}
        },
        "wheel_publications": {"wheel": list(first)},
    }
    assert closed_event_blocks_publications(closed_state, "wheel", first)
    assert prune_closed_publications(closed_state) == 1
    assert not closed_state["wheel_publications"]
    newer = [dict(first[0], message_date="2026-07-14T13:00:00+00:00")]
    assert not closed_event_blocks_publications(closed_state, "wheel", newer)
    closed_state["inactive_wheels"]["wheel"] = {
        "marked_at": "2026-07-14T12:00:00+00:00"
    }
    assert closed_event_blocks_publications(closed_state, "wheel", newer)
    print("wheel publication merge v2 self-test passed")


if __name__ == "__main__":
    self_test()
