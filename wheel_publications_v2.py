from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable


UTC = timezone.utc

WHEEL_TYPE_NORMAL = "normal"
WHEEL_TYPE_REFERRAL = "referral"
WHEEL_TYPE_SUSPECTED_REFERRAL = "suspected_referral"
WHEEL_TYPES = {
    WHEEL_TYPE_NORMAL,
    WHEEL_TYPE_REFERRAL,
    WHEEL_TYPE_SUSPECTED_REFERRAL,
}
REFERRAL_IDENTIFIER_HISTORY_KEY = "referral_identifier_history"
REFERRAL_IDENTIFIER_HISTORY_LIMIT = 500
PAGE_REFERRAL_HINT_RE = re.compile(
    r"(?:^|[;\s])page_referral_hint="
    r"(?P<classification>referral|suspected_referral)(?:;|$)",
    re.IGNORECASE,
)

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
SUSPECTED_REFERRAL_SHORT_HTML = "🟡 <b>Предположительно реферальное колесо</b>"
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
_REFERRAL_HINT_RE = re.compile(
    r"\b(?:реф(?:ерал\w*|ов|ы)?|referral\w*)\b",
    re.IGNORECASE,
)


def is_referral_restricted(text: str) -> bool:
    """Recognize an explicit referral/promo eligibility restriction in a post."""

    value = " ".join(str(text or "").split())
    return bool(
        value and any(pattern.search(value) for pattern in _REFERRAL_RESTRICTION_PATTERNS)
    )


def entry_is_referral_restricted(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("referral_restricted") is True:
        return True
    return is_referral_restricted(str(entry.get("message_text") or ""))


def referral_classification(value: Any) -> str:
    """Classify referral scope without inferring per-account eligibility.

    ``referral`` requires explicit publication/state evidence. A weaker referral
    mention is kept as ``suspected_referral`` and must never be promoted to an
    account-level ``referral_ineligible`` result.
    """

    if isinstance(value, dict):
        if entry_is_referral_restricted(value):
            return WHEEL_TYPE_REFERRAL
        explicit = str(
            value.get("wheel_type")
            or value.get("referral_classification")
            or ""
        ).strip().casefold()
        if explicit in WHEEL_TYPES:
            return explicit
        if value.get("referral_suspected") is True:
            return WHEEL_TYPE_SUSPECTED_REFERRAL
        text = str(value.get("message_text") or "")
    else:
        text = str(value or "")

    if is_referral_restricted(text):
        return WHEEL_TYPE_REFERRAL
    normalized = " ".join(text.split())
    if normalized and _REFERRAL_HINT_RE.search(normalized):
        return WHEEL_TYPE_SUSPECTED_REFERRAL
    return WHEEL_TYPE_NORMAL


def referral_classification_evidence(value: Any) -> str:
    classification = referral_classification(value)
    if classification == WHEEL_TYPE_REFERRAL:
        if isinstance(value, dict) and value.get("referral_restricted") is True:
            return "explicit_persisted_referral_restriction"
        return "explicit_publication_referral_restriction"
    if classification == WHEEL_TYPE_SUSPECTED_REFERRAL:
        if isinstance(value, dict):
            explicit = str(value.get("referral_classification_evidence") or "").strip()
            if explicit:
                return explicit
        return "publication_referral_hint_without_explicit_restriction"
    return ""


def page_referral_hint(text: str) -> str:
    """Return a privacy-safe machine hint for referral evidence on BetBoom."""

    classification = referral_classification(str(text or ""))
    if classification == WHEEL_TYPE_REFERRAL:
        return "page_referral_hint=referral;page_explicit_referral_restriction"
    if classification == WHEEL_TYPE_SUSPECTED_REFERRAL:
        return "page_referral_hint=suspected_referral;page_referral_banner_hint"
    return ""


def page_referral_classification_from_detail(detail: Any) -> str:
    match = PAGE_REFERRAL_HINT_RE.search(str(detail or ""))
    if not match:
        return WHEEL_TYPE_NORMAL
    value = str(match.group("classification") or "").casefold()
    return value if value in WHEEL_TYPES else WHEEL_TYPE_NORMAL


def _entry_identifier(entry: Any, fallback: Any = "") -> str:
    if not isinstance(entry, dict):
        return str(fallback or "").strip().casefold()
    return str(
        entry.get("wheel_key") or entry.get("identifier") or fallback or ""
    ).strip().casefold()


def _history_signal_from_state(
    state: dict[str, Any],
    identifier: str,
) -> tuple[str, str, str]:
    """Find prior referral evidence for one identifier without using failures."""

    history = state.get(REFERRAL_IDENTIFIER_HISTORY_KEY)
    stored = history.get(identifier) if isinstance(history, dict) else None
    if isinstance(stored, dict):
        classification = str(stored.get("classification") or "").casefold()
        if classification in {
            WHEEL_TYPE_REFERRAL,
            WHEEL_TYPE_SUSPECTED_REFERRAL,
        }:
            return (
                classification,
                str(stored.get("evidence") or "identifier_history"),
                str(stored.get("last_seen_at") or ""),
            )

    candidates: list[dict[str, Any]] = []
    for collection_name in (
        "active_wheels",
        "recently_completed_wheels",
        "inactive_wheels",
    ):
        collection = state.get(collection_name)
        if not isinstance(collection, dict):
            continue
        for raw_key, raw in collection.items():
            if isinstance(raw, dict) and _entry_identifier(raw, raw_key) == identifier:
                candidates.append(raw)

    events = state.get("auto_participation_events")
    if isinstance(events, dict):
        for raw in events.values():
            if not isinstance(raw, dict):
                continue
            context = raw.get("event_context")
            if isinstance(context, dict) and _entry_identifier(context) == identifier:
                candidates.append(context)

    suspected: tuple[str, str, str] | None = None
    for candidate in candidates:
        classification = referral_classification(candidate)
        observed_at = str(
            candidate.get("message_date")
            or candidate.get("server_start_at")
            or candidate.get("last_checked_at")
            or ""
        )
        if classification == WHEEL_TYPE_REFERRAL:
            return (
                classification,
                "identifier_history_explicit_referral",
                observed_at,
            )
        if classification == WHEEL_TYPE_SUSPECTED_REFERRAL and suspected is None:
            suspected = (
                classification,
                "identifier_history_suspected_referral",
                observed_at,
            )
    return suspected or (WHEEL_TYPE_NORMAL, "", "")


def _prune_identifier_history(state: dict[str, Any]) -> None:
    history = state.get(REFERRAL_IDENTIFIER_HISTORY_KEY)
    if not isinstance(history, dict) or len(history) <= REFERRAL_IDENTIFIER_HISTORY_LIMIT:
        return
    ordered = sorted(
        history,
        key=lambda key: str(
            history.get(key, {}).get("last_seen_at")
            if isinstance(history.get(key), dict)
            else ""
        ),
        reverse=True,
    )
    for key in ordered[REFERRAL_IDENTIFIER_HISTORY_LIMIT:]:
        history.pop(key, None)


def apply_referral_context(
    state: dict[str, Any],
    entry: dict[str, Any],
    *,
    observed_at: Any = None,
    browser_detail: Any = "",
) -> str:
    """Classify current evidence and cautiously remember identifier history."""

    identifier = _entry_identifier(entry)
    direct = referral_classification(entry)
    page = page_referral_classification_from_detail(browser_detail)
    history_classification, history_evidence, history_seen_at = (
        _history_signal_from_state(state, identifier)
        if identifier
        else (WHEEL_TYPE_NORMAL, "", "")
    )

    if direct == WHEEL_TYPE_REFERRAL:
        classification = WHEEL_TYPE_REFERRAL
        evidence = referral_classification_evidence(entry)
    elif page == WHEEL_TYPE_REFERRAL:
        classification = WHEEL_TYPE_REFERRAL
        evidence = "explicit_betboom_page_referral_restriction"
        entry["referral_restricted"] = True
    elif direct == WHEEL_TYPE_SUSPECTED_REFERRAL:
        classification = WHEEL_TYPE_SUSPECTED_REFERRAL
        evidence = referral_classification_evidence(entry)
    elif page == WHEEL_TYPE_SUSPECTED_REFERRAL:
        classification = WHEEL_TYPE_SUSPECTED_REFERRAL
        evidence = "betboom_page_referral_banner_hint"
    elif history_classification in {
        WHEEL_TYPE_REFERRAL,
        WHEEL_TYPE_SUSPECTED_REFERRAL,
    }:
        classification = WHEEL_TYPE_SUSPECTED_REFERRAL
        evidence = history_evidence or "identifier_history"
    else:
        classification = WHEEL_TYPE_NORMAL
        evidence = ""

    entry["wheel_type"] = classification
    if classification == WHEEL_TYPE_SUSPECTED_REFERRAL:
        entry["referral_suspected"] = True
    else:
        entry.pop("referral_suspected", None)
    if evidence:
        entry["referral_classification_evidence"] = evidence
    else:
        entry.pop("referral_classification_evidence", None)

    if identifier and classification in {
        WHEEL_TYPE_REFERRAL,
        WHEEL_TYPE_SUSPECTED_REFERRAL,
    }:
        history = state.setdefault(REFERRAL_IDENTIFIER_HISTORY_KEY, {})
        previous = history.get(identifier)
        record = dict(previous) if isinstance(previous, dict) else {}
        previous_classification = str(record.get("classification") or "")
        stored_classification = (
            WHEEL_TYPE_REFERRAL
            if WHEEL_TYPE_REFERRAL in {previous_classification, classification}
            else WHEEL_TYPE_SUSPECTED_REFERRAL
        )
        when = str(observed_at or history_seen_at or datetime.now(UTC).isoformat())
        record.update(
            {
                "identifier": identifier,
                "classification": stored_classification,
                "evidence": evidence,
                "last_seen_at": when,
            }
        )
        record.setdefault("first_seen_at", when)
        history[identifier] = record
        _prune_identifier_history(state)
    return classification


def referral_restriction_notice(text: str, *, html_mode: bool = True) -> str:
    if not is_referral_restricted(text):
        return ""
    return (
        REFERRAL_RESTRICTED_NOTICE_HTML
        if html_mode
        else REFERRAL_RESTRICTED_NOTICE_TEXT
    )


def referral_classification_notice(value: Any, *, html_mode: bool = True) -> str:
    classification = referral_classification(value)
    if classification == WHEEL_TYPE_REFERRAL:
        return (
            "🎯 <b>Реферальное колесо</b>\n"
            "Доступность будет проверена отдельно для каждого аккаунта."
            if html_mode
            else "Реферальное колесо. Доступность проверяется отдельно для каждого аккаунта."
        )
    if classification == WHEEL_TYPE_SUSPECTED_REFERRAL:
        return (
            "🟡 <b>Предположительно реферальное колесо</b>\n"
            "Ограничение ещё не подтверждено BetBoom."
            if html_mode
            else "Предположительно реферальное колесо. Ограничение ещё не подтверждено BetBoom."
        )
    return ""


def _clean_source(value: Any) -> str:
    return str(value or "").strip().lstrip("@")


def _publication_key(row: dict[str, Any]) -> tuple[str, int, str]:
    source = _clean_source(row.get("source")).casefold()
    try:
        message_id = int(row.get("message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    return source, message_id, str(row.get("message_url") or "")


def _normalized_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source = _clean_source(row.get("source"))
    if not source:
        return None
    try:
        message_id = int(row.get("message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    result: dict[str, Any] = {
        "source": source,
        "message_id": message_id,
        "message_date": str(row.get("message_date") or row.get("created_at") or ""),
        "message_url": str(row.get("message_url") or ""),
    }
    if "has_future_deadline" in row:
        result["has_future_deadline"] = bool(row.get("has_future_deadline"))
    if "has_future_availability" in row:
        result["has_future_availability"] = bool(row.get("has_future_availability"))
    return result


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


def publication_sources(
    state: dict[str, Any], key: str, fallback: Any = None
) -> list[str]:
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
    if isinstance(state.get("inactive_wheels", {}).get(normalized), dict):
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


def _preliminary_alert_exists(state: dict[str, Any], key: str) -> bool:
    alerts = state.get("url_alerts")
    return isinstance(alerts, dict) and str(key).casefold() in alerts


def _any_notification_suppressed(
    original_suppressed: Callable,
    original_activation_suppressed: Callable,
    state: dict[str, Any],
    link: str,
    key: str,
) -> bool:
    """Treat an actually recorded preliminary alert as the same event delivery."""

    return bool(
        original_activation_suppressed(state, link)
        or (
            _preliminary_alert_exists(state, key)
            and original_suppressed(state, link)
        )
    )


def install(monitor_module: Any, runtime_module: Any) -> None:
    """Persist every source while delivering only one alert per wheel generation."""

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
        return _any_notification_suppressed(
            original_suppressed,
            original_activation_suppressed,
            state,
            link,
            monitor_module.wheel_key(link),
        )

    base_runtime._persist_publications = persist_merged
    monitor_module.load_state = load_state_without_closed_publications
    monitor_module.is_suppressed = is_suppressed_with_publications
    monitor_module.is_activation_suppressed = is_activation_suppressed_with_publications
    base_runtime._bbvg_publication_merge_v2_installed = True
    monitor_module._bbvg_publication_merge_v2_installed = True


def self_test() -> None:
    assert is_referral_restricted(
        "Колесо для рефов на BetBoom https://betboom.ru/freestream/CTOM13"
    )
    assert "Колесо только для рефералов" in referral_restriction_notice(
        "Колесо для рефов"
    )
    assert referral_classification("Колесо для рефов") == WHEEL_TYPE_REFERRAL
    assert (
        referral_classification("Реферальный розыгрыш BetBoom")
        == WHEEL_TYPE_SUSPECTED_REFERRAL
    )
    assert referral_classification("Колесо для всех") == WHEEL_TYPE_NORMAL

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

    assert _any_notification_suppressed(
        lambda _state, _link: True,
        lambda _state, _link: False,
        {"url_alerts": {"wheel": {"alerted_at": "now"}}},
        "wheel",
        "wheel",
    )
    assert _any_notification_suppressed(
        lambda _state, _link: False,
        lambda _state, _link: True,
        {},
        "wheel",
        "wheel",
    )
    assert not _any_notification_suppressed(
        lambda _state, _link: True,
        lambda _state, _link: False,
        {},
        "wheel",
        "wheel",
    )
    print("wheel publication merge v2 self-test passed")


if __name__ == "__main__":
    self_test()
