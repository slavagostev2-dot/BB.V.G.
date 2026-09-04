from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


UTC = timezone.utc

WHEEL_TYPE_NORMAL = "normal"
WHEEL_TYPE_REFERRAL = "referral"
# Compatibility-only name for old callers. New classification never emits it.
WHEEL_TYPE_SUSPECTED_REFERRAL = "suspected_referral"
WHEEL_TYPES = {
    WHEEL_TYPE_NORMAL,
    WHEEL_TYPE_REFERRAL,
}
REFERRAL_IDENTIFIER_HISTORY_KEY = "referral_identifier_history"
REFERRAL_IDENTIFIER_HISTORY_LIMIT = 500
STRONG_REFERRAL_EVIDENCE = "betboom_referral_ineligible"
_REFERRAL_INELIGIBLE_DETAIL_MARKER = "referral_ineligible_exact_text:"

REFERRAL_RESTRICTED_NOTICE_TEXT = (
    "Колесо только для рефералов. BetBoom подтвердил реферальное ограничение "
    "для одного из проверенных аккаунтов."
)
REFERRAL_RESTRICTED_NOTICE_HTML = (
    "⚠️ <b>Реферальное колесо</b>\n"
    "BetBoom подтвердил реферальное ограничение для одного из проверенных аккаунтов."
)
REFERRAL_RESTRICTED_SHORT_HTML = "⚠️ <b>Реферальное колесо</b>"
# Kept until the follow-up cleanup removes old presentation branches.
SUSPECTED_REFERRAL_SHORT_HTML = "🟡 <b>Предположительно реферальное колесо</b>"


def is_referral_restricted(text: str) -> bool:
    """Legacy publication-text hook; Telegram text is never referral proof."""

    return False


def _persisted_referral_is_confirmed(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    classification = str(
        entry.get("wheel_type") or entry.get("referral_classification") or ""
    ).strip().casefold()
    evidence = str(entry.get("referral_classification_evidence") or "").strip()
    return classification == WHEEL_TYPE_REFERRAL and evidence == STRONG_REFERRAL_EVIDENCE


def entry_is_referral_restricted(entry: Any) -> bool:
    """Return true only for a persisted, strongly confirmed BetBoom restriction."""

    return _persisted_referral_is_confirmed(entry)


def referral_classification(value: Any) -> str:
    """Classify referral scope using strong BetBoom evidence only.

    Telegram publication text, generic page text, old ``referral_restricted`` flags,
    identifier history and ``suspected_referral`` are intentionally not evidence.
    """

    return (
        WHEEL_TYPE_REFERRAL
        if _persisted_referral_is_confirmed(value)
        else WHEEL_TYPE_NORMAL
    )


def referral_classification_evidence(value: Any) -> str:
    return (
        STRONG_REFERRAL_EVIDENCE
        if referral_classification(value) == WHEEL_TYPE_REFERRAL
        else ""
    )


def page_referral_hint(text: str) -> str:
    """Keep page text diagnostic-only; it must never classify a wheel."""

    return ""


def page_referral_classification_from_detail(detail: Any) -> str:
    """Recognize only the internal marker created by explicit BetBoom refusal."""

    return (
        WHEEL_TYPE_REFERRAL
        if _REFERRAL_INELIGIBLE_DETAIL_MARKER in str(detail or "")
        else WHEEL_TYPE_NORMAL
    )


def _entry_identifier(entry: Any, fallback: Any = "") -> str:
    if not isinstance(entry, dict):
        return str(fallback or "").strip().casefold()
    return str(
        entry.get("wheel_key") or entry.get("identifier") or fallback or ""
    ).strip().casefold()


def _same_generation(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _entry_identifier(left) != _entry_identifier(right):
        return False

    for field in ("canonical_event_id", "event_id", "generation_id"):
        left_value = str(left.get(field) or "").strip()
        right_value = str(right.get(field) or "").strip()
        if left_value and right_value:
            return left_value == right_value

    left_action = str(left.get("action_id") or "").strip()
    right_action = str(right.get("action_id") or "").strip()
    if left_action and right_action and left_action != right_action:
        return False

    left_start = str(left.get("server_start_at") or "").strip()
    right_start = str(right.get("server_start_at") or "").strip()
    if left_start and right_start and left_start != right_start:
        return False

    # Secondary-account candidates are copies of the current active entry. If
    # neither side has a complete server identity, matching identifier plus every
    # available identity component is the safest compatibility fallback.
    return bool(_entry_identifier(left))


def _mark_confirmed_referral(entry: dict[str, Any]) -> None:
    entry["wheel_type"] = WHEEL_TYPE_REFERRAL
    entry["referral_restricted"] = True
    entry["referral_classification_evidence"] = STRONG_REFERRAL_EVIDENCE
    entry.pop("referral_suspected", None)


def _mark_normal_referral_state(entry: dict[str, Any]) -> None:
    entry["wheel_type"] = WHEEL_TYPE_NORMAL
    entry.pop("referral_restricted", None)
    entry.pop("referral_suspected", None)
    entry.pop("referral_classification_evidence", None)


def apply_referral_context(
    state: dict[str, Any],
    entry: dict[str, Any],
    *,
    observed_at: Any = None,
    browser_detail: Any = "",
) -> str:
    """Persist referral only after an explicit BetBoom account-level refusal."""

    del observed_at  # retained in the call contract for compatibility
    identifier = _entry_identifier(entry)
    confirmed = _persisted_referral_is_confirmed(entry) or (
        page_referral_classification_from_detail(browser_detail)
        == WHEEL_TYPE_REFERRAL
    )

    if not confirmed:
        _mark_normal_referral_state(entry)
        return WHEEL_TYPE_NORMAL

    _mark_confirmed_referral(entry)

    active = state.get("active_wheels")
    current = active.get(identifier) if isinstance(active, dict) and identifier else None
    if isinstance(current, dict) and current is not entry and _same_generation(entry, current):
        _mark_confirmed_referral(current)
    return WHEEL_TYPE_REFERRAL


def referral_restriction_notice(text: str, *, html_mode: bool = True) -> str:
    """Publication text alone no longer produces a referral notice."""

    return ""


def referral_classification_notice(value: Any, *, html_mode: bool = True) -> str:
    classification = referral_classification(value)
    if classification == WHEEL_TYPE_REFERRAL:
        return (
            "🎯 <b>Реферальное колесо</b>\n"
            "BetBoom подтвердил реферальное ограничение; доступность проверяется "
            "отдельно для каждого аккаунта."
            if html_mode
            else "Реферальное колесо. BetBoom подтвердил ограничение; доступность проверяется отдельно для каждого аккаунта."
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
    referral_post = "Колесо для рефов на BetBoom https://betboom.ru/freestream/CTOM13"
    assert not is_referral_restricted(referral_post)
    assert referral_restriction_notice(referral_post) == ""
    assert referral_classification(referral_post) == WHEEL_TYPE_NORMAL
    assert referral_classification("Реферальный розыгрыш BetBoom") == WHEEL_TYPE_NORMAL
    assert referral_classification("Колесо для всех") == WHEEL_TYPE_NORMAL
    assert page_referral_hint("реферальное колесо только для рефералов") == ""
    assert page_referral_classification_from_detail(
        "page_referral_hint=referral;page_explicit_referral_restriction"
    ) == WHEEL_TYPE_NORMAL

    confirmed = {
        "identifier": "confirmed",
        "wheel_type": WHEEL_TYPE_REFERRAL,
        "referral_restricted": True,
        "referral_classification_evidence": STRONG_REFERRAL_EVIDENCE,
    }
    assert entry_is_referral_restricted(confirmed)
    assert referral_classification(confirmed) == WHEEL_TYPE_REFERRAL

    active = {
        "wheel_key": "ref-one",
        "identifier": "ref-one",
        "action_id": 42,
        "server_start_at": "2026-09-05T00:00:00+00:00",
        "message_text": "обычная публикация",
    }
    state = {"active_wheels": {"ref-one": active}}
    candidate = dict(active)
    classification = apply_referral_context(
        state,
        candidate,
        browser_detail=(
            "referral_ineligible_exact_text:main:"
            "Ваш аккаунт не является рефералом"
        ),
    )
    assert classification == WHEEL_TYPE_REFERRAL
    assert referral_classification(candidate) == WHEEL_TYPE_REFERRAL
    assert referral_classification(active) == WHEEL_TYPE_REFERRAL
    assert active["referral_classification_evidence"] == STRONG_REFERRAL_EVIDENCE

    legacy = {
        "identifier": "legacy",
        "message_text": "Колесо только для рефералов",
        "referral_restricted": True,
        "wheel_type": WHEEL_TYPE_SUSPECTED_REFERRAL,
        "referral_suspected": True,
        "referral_classification_evidence": "identifier_history_explicit_referral",
    }
    assert apply_referral_context({}, legacy) == WHEEL_TYPE_NORMAL
    assert legacy["wheel_type"] == WHEEL_TYPE_NORMAL
    assert "referral_restricted" not in legacy
    assert "referral_suspected" not in legacy

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
