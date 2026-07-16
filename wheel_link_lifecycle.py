from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


UTC = timezone.utc
UNTIMED_WINDOW = timedelta(hours=2)


@dataclass(frozen=True)
class LinkWindow:
    blocked: bool
    block_until: datetime | None
    source: str


def _as_utc(value: Any, parser: Callable[[Any], datetime | None]) -> datetime | None:
    parsed = parser(value)
    if parsed is None:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _record(state: dict[str, Any], name: str, key: str) -> dict[str, Any] | None:
    collection = state.get(name)
    if not isinstance(collection, dict):
        return None
    value = collection.get(key)
    return value if isinstance(value, dict) else None


def _first_seen(
    state: dict[str, Any],
    key: str,
    parser: Callable[[Any], datetime | None],
) -> datetime | None:
    for name, fields in (
        ("active_wheels", ("first_notified_at", "message_date", "last_notification_at")),
        ("url_alerts", ("alerted_at",)),
        ("activation_alerts", ("alerted_at",)),
    ):
        row = _record(state, name, key)
        if row is None:
            continue
        for field in fields:
            value = _as_utc(row.get(field), parser)
            if value is not None:
                return value
    return None


def _manual_deadline(
    state: dict[str, Any],
    key: str,
    parser: Callable[[Any], datetime | None],
) -> datetime | None:
    row = _record(state, "manual_deadlines", key)
    if row is None:
        return None
    return _as_utc(row.get("deadline"), parser)


def _stored_deadline(
    state: dict[str, Any],
    key: str,
    parser: Callable[[Any], datetime | None],
) -> tuple[datetime | None, str]:
    manual = _manual_deadline(state, key, parser)
    if manual is not None:
        return manual, "manual_timer"
    active = _record(state, "active_wheels", key)
    if active is not None:
        value = _as_utc(active.get("deadline"), parser)
        if value is not None:
            return value, "timer"
    alert = _record(state, "url_alerts", key)
    if alert is not None:
        value = _as_utc(alert.get("deadline"), parser)
        if value is not None:
            return value, "timer"
    return None, ""


def link_window(
    state: dict[str, Any],
    key: str,
    *,
    current: datetime,
    parser: Callable[[Any], datetime | None],
) -> LinkWindow:
    normalized = str(key or "").casefold()
    current = current.astimezone(UTC) if current.tzinfo else current.replace(tzinfo=UTC)
    deadline, source = _stored_deadline(state, normalized, parser)
    if deadline is not None:
        return LinkWindow(current < deadline, deadline, source)

    first_seen = _first_seen(state, normalized, parser)
    if first_seen is None:
        return LinkWindow(False, None, "new")
    block_until = first_seen + UNTIMED_WINDOW
    return LinkWindow(current < block_until, block_until, "no_timer")


def rotate_expired_event(
    state: dict[str, Any],
    key: str,
    *,
    current: datetime,
    block_until: datetime | None,
) -> bool:
    normalized = str(key or "").casefold()
    active = state.setdefault("active_wheels", {})
    old = active.pop(normalized, None) if isinstance(active, dict) else None
    changed = isinstance(old, dict)
    if isinstance(old, dict):
        archived = dict(old)
        archived["removed_at"] = (block_until or current).astimezone(UTC).isoformat()
        archived["completion_reason"] = "link_reuse_window_expired"
        state.setdefault("recently_completed_wheels", {})[normalized] = archived

    for name in (
        "url_alerts",
        "activation_alerts",
        "manual_deadlines",
        "manual_overrides",
        "participating_wheels",
        "completed_wheel_alerts",
    ):
        collection = state.get(name)
        if isinstance(collection, dict) and collection.pop(normalized, None) is not None:
            changed = True

    publications = state.get("wheel_publications")
    if isinstance(publications, dict) and publications.pop(normalized, None) is not None:
        changed = True
    return changed


def prepare_link(
    monitor_module: Any,
    state: dict[str, Any] | None,
    link: str,
    *,
    current: datetime | None = None,
) -> LinkWindow:
    if not isinstance(state, dict):
        return LinkWindow(False, None, "new")
    key = monitor_module.wheel_key(link)
    now = current or monitor_module.now_utc()
    window = link_window(
        state,
        key,
        current=now,
        parser=monitor_module.parse_datetime,
    )
    if not window.blocked and window.block_until is not None:
        rotate_expired_event(
            state,
            key,
            current=now,
            block_until=window.block_until,
        )
    return window


def _remember_window(
    monitor_module: Any,
    state: dict[str, Any],
    link: str,
    deadline: datetime | None,
    *,
    activation: bool,
) -> None:
    now = monitor_module.now_utc()
    key = monitor_module.wheel_key(link)
    until = deadline.astimezone(UTC) if deadline is not None else now + UNTIMED_WINDOW
    collection_name = "activation_alerts" if activation else "url_alerts"
    entry = state.setdefault(collection_name, {}).setdefault(key, {})
    entry.update(
        {
            "identifier": monitor_module.wheel_identifier(link),
            "url": monitor_module.normalize_url(link),
            "alerted_at": now.isoformat(),
            "suppress_until": until.isoformat(),
            "lifecycle_rule": "timer" if deadline is not None else "no_timer_2h",
        }
    )
    if deadline is not None:
        entry["deadline"] = deadline.astimezone(UTC).isoformat()
    else:
        entry.pop("deadline", None)


def apply_manual_deadline(
    state: dict[str, Any],
    key: str,
    deadline: datetime,
    *,
    current: datetime,
) -> None:
    normalized = str(key or "").casefold()
    deadline = deadline.astimezone(UTC)
    current = current.astimezone(UTC)
    entry = state.setdefault("url_alerts", {}).setdefault(normalized, {})
    entry.update(
        {
            "alerted_at": str(entry.get("alerted_at") or current.isoformat()),
            "deadline": deadline.isoformat(),
            "suppress_until": deadline.isoformat(),
            "lifecycle_rule": "manual_timer",
        }
    )
    activation = state.setdefault("activation_alerts", {}).get(normalized)
    if isinstance(activation, dict):
        activation["deadline"] = deadline.isoformat()
        activation["suppress_until"] = deadline.isoformat()
        activation["lifecycle_rule"] = "manual_timer"


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_wheel_link_lifecycle_installed", False):
        return

    original_suppressed: Callable = monitor_module.is_suppressed
    original_activation_suppressed: Callable = monitor_module.is_activation_suppressed
    original_remember_alert: Callable = monitor_module.remember_alert
    original_remember_activation: Callable = monitor_module.remember_activation
    original_assess_new: Callable = monitor_module.assess_new_wheel
    original_assess_pending: Callable = monitor_module.assess_pending_wheel

    def suppressed(state: dict[str, Any], link: str) -> bool:
        # Preserve publication bookkeeping performed by the previous wrapper.
        try:
            original_suppressed(state, link)
        except Exception:
            pass
        return prepare_link(monitor_module, state, link).blocked

    def activation_suppressed(state: dict[str, Any], link: str) -> bool:
        try:
            original_activation_suppressed(state, link)
        except Exception:
            pass
        return prepare_link(monitor_module, state, link).blocked

    def remember_alert(state: dict[str, Any], link: str, deadline: datetime | None) -> None:
        original_remember_alert(state, link, deadline)
        _remember_window(monitor_module, state, link, deadline, activation=False)

    def remember_activation(state: dict[str, Any], link: str, deadline: datetime | None) -> None:
        original_remember_activation(state, link, deadline)
        _remember_window(monitor_module, state, link, deadline, activation=True)

    def assess_new(message: Any, link: str, state: dict[str, Any] | None = None):
        result = original_assess_new(message, link, state)
        if result.status in {"inactive", "duplicate_action"}:
            return result
        window = prepare_link(monitor_module, state, link)
        if window.blocked:
            return monitor_module.WheelAssessment(
                False,
                window.block_until if window.source != "no_timer" else None,
                "повторная ссылка в пределах текущего события",
                "duplicate_link",
                result.page_excerpt,
                action_id=result.action_id,
                available_at=result.available_at,
                verification_status=result.verification_status,
            )
        return result

    def assess_pending(message: Any, link: str, state: dict[str, Any] | None = None):
        result = original_assess_pending(message, link, state)
        if result.status in {"inactive", "duplicate_action"}:
            return result
        window = prepare_link(monitor_module, state, link)
        if window.blocked:
            return monitor_module.WheelAssessment(
                False,
                window.block_until if window.source != "no_timer" else None,
                "повторная ссылка в пределах текущего события",
                "duplicate_link",
                result.page_excerpt,
                action_id=result.action_id,
                available_at=result.available_at,
                verification_status=result.verification_status,
            )
        return result

    monitor_module.UNKNOWN_DEDUP_HOURS = 2
    monitor_module.is_suppressed = suppressed
    monitor_module.is_activation_suppressed = activation_suppressed
    monitor_module.remember_alert = remember_alert
    monitor_module.remember_activation = remember_activation
    monitor_module.assess_new_wheel = assess_new
    monitor_module.assess_pending_wheel = assess_pending
    monitor_module._bbvg_wheel_link_lifecycle_installed = True


def self_test() -> None:
    import monitor

    base = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    key = "same"

    timed = {
        "active_wheels": {
            key: {
                "first_notified_at": base.isoformat(),
                "deadline": (base + timedelta(hours=3)).isoformat(),
            }
        }
    }
    assert link_window(timed, key, current=base + timedelta(hours=1), parser=monitor.parse_datetime).blocked
    after_timer = link_window(
        timed, key, current=base + timedelta(hours=3, seconds=1), parser=monitor.parse_datetime
    )
    assert not after_timer.blocked and after_timer.source == "timer"

    untimed = {"active_wheels": {key: {"first_notified_at": base.isoformat()}}}
    assert link_window(
        untimed, key, current=base + timedelta(minutes=119), parser=monitor.parse_datetime
    ).blocked
    assert not link_window(
        untimed, key, current=base + timedelta(minutes=121), parser=monitor.parse_datetime
    ).blocked

    manual = {
        "active_wheels": {key: {"first_notified_at": base.isoformat()}},
        "manual_deadlines": {
            key: {
                "deadline": (base + timedelta(hours=5)).isoformat(),
                "updated_at": (base + timedelta(hours=1)).isoformat(),
            }
        },
    }
    decision = link_window(
        manual, key, current=base + timedelta(hours=3), parser=monitor.parse_datetime
    )
    assert decision.blocked and decision.source == "manual_timer"

    reused = {
        "active_wheels": {key: {"first_notified_at": base.isoformat()}},
        "url_alerts": {key: {"alerted_at": base.isoformat()}},
        "participating_wheels": {key: {"marked_at": base.isoformat()}},
        "wheel_publications": {key: [{"source": "a", "message_id": 1}]},
    }
    assert rotate_expired_event(
        reused,
        key,
        current=base + timedelta(hours=3),
        block_until=base + UNTIMED_WINDOW,
    )
    assert key not in reused["active_wheels"]
    assert key not in reused["participating_wheels"]
    assert key not in reused["wheel_publications"]
    assert reused["recently_completed_wheels"][key]["completion_reason"] == "link_reuse_window_expired"

    manual_state: dict[str, Any] = {"url_alerts": {key: {"alerted_at": base.isoformat()}}}
    manual_deadline = base + timedelta(hours=4)
    apply_manual_deadline(manual_state, key, manual_deadline, current=base + timedelta(minutes=30))
    assert manual_state["url_alerts"][key]["suppress_until"] == manual_deadline.isoformat()
    assert manual_state["url_alerts"][key]["lifecycle_rule"] == "manual_timer"
    print("wheel link lifecycle scenario self-test passed")


if __name__ == "__main__":
    self_test()
