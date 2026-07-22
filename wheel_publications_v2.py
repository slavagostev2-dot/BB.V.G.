from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable


UTC = timezone.utc

REFERRAL_RESTRICTED_NOTICE_TEXT = (
    "Колесо только для рефералов. Для участия аккаунт должен быть зарегистрирован "
    "по реферальной ссылке или промокоду автора."
)
REFERRAL_RESTRICTED_NOTICE_HTML = (
    "⚠️ <b>Колесо только для рефералов</b>\n"
    "Для участия аккаунт должен быть зарегистрирован по реферальной ссылке "
    "или промокоду автора."
)
REFERRAL_RESTRICTED_SHORT_HTML = "⚠️ <b>Колесо только для рефералов</b>"
_REFERRAL_RESTRICTION_PATTERNS = (
    re.compile(r"\bтолько\s+(?:для\s+)?реф(?:ерал\w*|ов)\b", re.IGNORECASE),
    re.compile(r"\b(?:для|моим?|нашим?)\s+реферал\w*\b", re.IGNORECASE),
    re.compile(r"\b(?:колес\w*\s+)?для\s+рефов\b", re.IGNORECASE),
    re.compile(
        r"\b(?:участ\w*|доступ\w*|колес\w*)[^.\n]{0,140}"
        r"\b(?:только|лишь|исключительно)[^.\n]{0,140}"
        r"\b(?:реферал\w*|реферальн\w*\s+ссылк\w*|промокод\w*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:участ\w*|доступ\w*)[^.\n]{0,140}"
        r"\b(?:зарегистрирован\w*|регистрац\w*)[^.\n]{0,120}"
        r"\b(?:по|через)\s+(?:моей\s+|наш\w*\s+)?"
        r"(?:реферальн\w*\s+)?(?:ссылк\w*|промокод\w*)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:only\s+for\s+referrals?|referral[-\s]?only)\b", re.IGNORECASE),
)


def is_referral_restricted(text: str) -> bool:
    """Recognize an explicit referral/promo eligibility restriction in a post."""

    value = " ".join(str(text or "").split())
    return bool(value and any(pattern.search(value) for pattern in _REFERRAL_RESTRICTION_PATTERNS))


def entry_is_referral_restricted(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("referral_restricted") is True:
        return True
    return is_referral_restricted(str(entry.get("message_text") or ""))


def auto_participation_allowed(entry: Any) -> bool:
    """Allow browser dispatch only for wheels without referral restrictions."""

    return not entry_is_referral_restricted(entry)


def referral_restriction_notice(text: str, *, html_mode: bool = True) -> str:
    if not is_referral_restricted(text):
        return ""
    return REFERRAL_RESTRICTED_NOTICE_HTML if html_mode else REFERRAL_RESTRICTED_NOTICE_TEXT


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
        **(
            {"has_future_availability": bool(row.get("has_future_availability"))}
            if "has_future_availability" in row
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
        if row.get("has_future_availability"):
            previous["has_future_availability"] = True

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
        raw_sources = fallback.get("sources")
        if isinstance(raw_sources, list):
            result.extend(_clean_source(source) for source in raw_sources)
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
    """Persist publications and stop referral wheels before browser dispatch."""

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

        incoming_rows = base_runtime._WHEEL_PUBLICATIONS.get(normalized, [])
        if closed_event_blocks_publications(state, normalized, incoming_rows):
            collection.pop(normalized, None)
            return

        original(state, normalized, fallback)
        incoming = collection.get(normalized, [])

        # Never drop previously observed publications merely because active_wheels
        # is temporarily rebuilt during one monitor cycle. Old event publications
        # are already removed explicitly by prune_closed_publications(), so keeping
        # the accumulated rows here is safe and preserves multi-source attribution.
        merged = merge_publications(previous, incoming, reset_event=False)
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

    # The normal event dispatcher calls this function before it starts the
    # auto-participation workflow. Blocking here prevents both configured
    # BetBoom accounts and recovery from reaching Playwright for referral wheels.
    import betboom_auto_participation

    original_eligible: Callable = betboom_auto_participation._eligible_for_event_attempt

    def eligible_without_referral(entry: dict[str, Any], monitor: Any, current: Any) -> bool:
        if not auto_participation_allowed(entry):
            return False
        return bool(original_eligible(entry, monitor, current))

    betboom_auto_participation._eligible_for_event_attempt = eligible_without_referral
    betboom_auto_participation._bbvg_referral_guard_installed = True

    base_runtime._persist_publications = persist_merged
    monitor_module.load_state = load_state_without_closed_publications
    monitor_module.is_suppressed = is_suppressed_with_publications
    monitor_module.is_activation_suppressed = is_activation_suppressed_with_publications
    base_runtime._bbvg_publication_merge_v2_installed = True
    monitor_module._bbvg_publication_merge_v2_installed = True


def self_test() -> None:
    referral_entry = {
        "url": "https://betboom.ru/freestream/CTOM13",
        "message_text": "Колесо для рефов на BetBoom",
    }
    regular_entry = {
        "url": "https://betboom.ru/freestream/regular",
        "message_text": "Обычное колесо BetBoom для всех",
    }
    assert is_referral_restricted(referral_entry["message_text"])
    assert not auto_participation_allowed(referral_entry)
    assert auto_participation_allowed(regular_entry)
    assert "Колесо только для рефералов" in referral_restriction_notice(
        "Колесо для рефов"
    )

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
    assert publication_sources(
        {"wheel_publications": {}},
        "wheel",
        {"source": "official", "sources": ["official", "collector"]},
    ) == ["official", "collector"]

    # A transient active_wheels rebuild must not erase a source already observed
    # for the same still-open event.
    transient_merge = merge_publications(merged, [dict(first[0])], reset_event=False)
    assert [row["source"] for row in transient_merge] == ["official", "collector"]

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
