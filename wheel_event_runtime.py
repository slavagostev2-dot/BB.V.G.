from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import wheel_lifecycle_v2


UTC = timezone.utc
EVENT_REUSE_GAP = timedelta(hours=6)
ACTIVE_WITHOUT_DRAW_TTL = timedelta(hours=2)
GENERATION_OBSERVATION_RETENTION = timedelta(days=14)
GENERATION_OBSERVATION_LIMIT = 1000
GENERATION_OBSERVATIONS_KEY = "wheel_generation_observations"

_START_CUE_RE = re.compile(
    r"\b(?:"
    r"запущу|запустим|запустят|запустится|"
    r"стартует|начн[её]тся|открою|откроется|"
    r"будет\s+доступн\w*|станет\s+доступн\w*|"
    r"можно\s+будет\s+(?:участвовать|зарегистрироваться)"
    r")\b",
    re.IGNORECASE,
)
_DRAW_CUE_RE = re.compile(
    r"\b(?:прокрут\w*|итог\w*|результат\w*|победител\w*|"
    r"закро\w*|заверш\w*)\b",
    re.IGNORECASE,
)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def infer_availability(
    text: str,
    published_at: datetime,
    deadline_parser: Callable[[str, datetime], tuple[datetime | None, str]],
) -> tuple[datetime | None, str]:
    """Recognize a future opening time without treating it as a draw deadline."""

    value = str(text or "")
    if not _START_CUE_RE.search(value) or _DRAW_CUE_RE.search(value):
        return None, ""
    available_at, method = deadline_parser(value, published_at)
    if available_at is None:
        return None, ""
    return available_at, f"время открытия из Telegram; {method}"


def _record_time(record: Any, *fields: str) -> datetime | None:
    if not isinstance(record, dict):
        return None
    for field in fields:
        parsed = _parse_datetime(record.get(field))
        if parsed is not None:
            return parsed
    return None


def _older_than_event(record: Any, event_at: datetime, *fields: str) -> bool:
    marker = _record_time(record, *fields)
    return marker is not None and marker < event_at


def reset_stale_event_state(
    state: dict[str, Any],
    key: str,
    event_at: datetime,
) -> list[str]:
    """Remove decisions that belong to an older use of the same freestream URL."""

    normalized = str(key or "").casefold()
    event_at = event_at.astimezone(UTC)
    removed: list[str] = []
    timestamp_fields = {
        "inactive_wheels": ("marked_at",),
        "recently_completed_wheels": ("removed_at", "confirmed_finished_at"),
        "completed_wheel_alerts": ("notified_at", "deadline"),
        "url_alerts": ("alerted_at",),
        "activation_alerts": ("alerted_at",),
        "manual_deadlines": ("updated_at",),
        "manual_overrides": ("updated_at", "created_at"),
        "participating_wheels": ("marked_at", "participating_at"),
    }
    for collection_name, fields in timestamp_fields.items():
        collection = state.get(collection_name)
        if not isinstance(collection, dict):
            continue
        record = collection.get(normalized)
        if _older_than_event(record, event_at, *fields):
            collection.pop(normalized, None)
            removed.append(collection_name)

    active = state.get("active_wheels")
    if isinstance(active, dict):
        record = active.get(normalized)
        active_at = _record_time(record, "message_date", "first_notified_at")
        if active_at is not None and event_at - active_at > EVENT_REUSE_GAP:
            active.pop(normalized, None)
            removed.append("active_wheels")

    publications = state.get("wheel_publications")
    if isinstance(publications, dict) and "active_wheels" in removed:
        publications.pop(normalized, None)
        removed.append("wheel_publications")
    return removed


def _record_action_id(record: Any) -> int | None:
    if not isinstance(record, dict):
        return None
    try:
        value = int(record.get("action_id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def generation_id(key: str, action_id: int | None, server_start_at: Any) -> str:
    """Stable identity for one authoritative BetBoom server start."""

    start = _parse_datetime(server_start_at)
    if action_id is None or start is None:
        return ""
    raw = "\x1f".join(
        (str(key or "").casefold(), str(int(action_id)), start.astimezone(UTC).isoformat())
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _observation_id(
    key: str,
    action_id: int | None,
    server_start_at: datetime | None,
) -> str:
    raw = "\x1f".join(
        (
            str(key or "").casefold(),
            str(action_id) if action_id is not None else "missing-action-id",
            server_start_at.isoformat() if server_start_at is not None else "missing-start",
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _prune_generation_observations(
    observations: dict[str, Any],
    *,
    current: datetime,
) -> None:
    cutoff = current - GENERATION_OBSERVATION_RETENTION
    for observation_id, raw in list(observations.items()):
        last_seen = _record_time(raw, "last_seen_at", "first_seen_at")
        if not isinstance(raw, dict) or last_seen is None or last_seen < cutoff:
            observations.pop(observation_id, None)
    overflow = len(observations) - GENERATION_OBSERVATION_LIMIT
    if overflow <= 0:
        return
    ordered = sorted(
        observations,
        key=lambda observation_id: str(
            (observations.get(observation_id) or {}).get("last_seen_at") or ""
        ),
    )
    for observation_id in ordered[:overflow]:
        observations.pop(observation_id, None)


def record_generation_observation(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
    *,
    current: datetime,
    status: str,
) -> str:
    """Persist a bounded, non-personal audit trail of BetBoom identities."""

    normalized = str(key or "").strip().casefold()
    if not normalized:
        return ""
    current = current.astimezone(UTC) if current.tzinfo else current.replace(tzinfo=UTC)
    parsed_start = _parse_datetime(server_start_at)
    parsed_action = _record_action_id({"action_id": action_id})
    observation_id = _observation_id(normalized, parsed_action, parsed_start)
    observations = state.setdefault(GENERATION_OBSERVATIONS_KEY, {})
    if not isinstance(observations, dict):
        observations = {}
        state[GENERATION_OBSERVATIONS_KEY] = observations
    previous = observations.get(observation_id)
    previous = previous if isinstance(previous, dict) else {}
    statuses = previous.get("statuses")
    statuses = dict(statuses) if isinstance(statuses, dict) else {}
    normalized_status = str(status or "observed").strip().casefold()[:40] or "observed"
    statuses[normalized_status] = int(statuses.get(normalized_status, 0) or 0) + 1
    generation = generation_id(normalized, parsed_action, parsed_start)
    observation: dict[str, Any] = {
        "wheel_key": normalized,
        "action_id": parsed_action,
        "server_start_at": parsed_start.isoformat() if parsed_start is not None else None,
        "first_seen_at": str(previous.get("first_seen_at") or current.isoformat()),
        "last_seen_at": current.isoformat(),
        "observations": int(previous.get("observations", 0) or 0) + 1,
        "statuses": statuses,
    }
    if generation:
        observation["generation_id"] = generation
    observations[observation_id] = observation
    _prune_generation_observations(observations, current=current)
    return observation_id


def generation_observation_report(
    state: dict[str, Any],
    *,
    current: datetime | None = None,
) -> dict[str, Any]:
    """Summarize evidence needed to decide whether the fallback rule is necessary."""

    current = current or datetime.now(UTC)
    raw_observations = state.get(GENERATION_OBSERVATIONS_KEY)
    observations = raw_observations if isinstance(raw_observations, dict) else {}
    links: dict[str, dict[str, Any]] = {}
    missing_identity: list[dict[str, Any]] = []
    for raw in observations.values():
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("wheel_key") or "").casefold()
        if not key:
            continue
        action_id = _record_action_id(raw)
        start = _parse_datetime(raw.get("server_start_at"))
        link = links.setdefault(key, {"action_ids": set(), "starts_by_action": {}})
        if action_id is not None:
            link["action_ids"].add(action_id)
            starts = link["starts_by_action"].setdefault(action_id, set())
            if start is not None:
                starts.add(start.isoformat())
        if action_id is None or start is None:
            missing_identity.append(
                {
                    "wheel_key": key,
                    "action_id": action_id,
                    "server_start_at": start.isoformat() if start is not None else None,
                    "first_seen_at": raw.get("first_seen_at"),
                    "last_seen_at": raw.get("last_seen_at"),
                    "observations": int(raw.get("observations", 0) or 0),
                }
            )

    repeated_action_ids: list[dict[str, Any]] = []
    multiple_action_ids: list[dict[str, Any]] = []
    for key, link in sorted(links.items()):
        action_ids = sorted(link["action_ids"])
        if len(action_ids) > 1:
            multiple_action_ids.append({"wheel_key": key, "action_ids": action_ids})
        for action_id, starts in sorted(link["starts_by_action"].items()):
            ordered_starts = sorted(starts)
            if len(ordered_starts) > 1:
                repeated_action_ids.append(
                    {
                        "wheel_key": key,
                        "action_id": action_id,
                        "server_start_at": ordered_starts,
                    }
                )
    return {
        "generated_at": current.astimezone(UTC).isoformat(),
        "retention_days": int(GENERATION_OBSERVATION_RETENTION.total_seconds() // 86400),
        "observation_identities": len(observations),
        "observed_links": len(links),
        "same_action_id_multiple_starts": repeated_action_ids,
        "same_link_multiple_action_ids": multiple_action_ids,
        "missing_server_identity": missing_identity,
    }


def _generation_records(
    state: dict[str, Any], key: str
) -> list[tuple[str, dict[str, Any]]]:
    normalized = str(key or "").casefold()
    rows: list[tuple[str, dict[str, Any]]] = []
    for collection_name in (
        "active_wheels",
        "inactive_wheels",
        "recently_completed_wheels",
        "wheel_action_history",
    ):
        collection = state.get(collection_name)
        record = collection.get(normalized) if isinstance(collection, dict) else None
        if isinstance(record, dict) and _record_action_id(record) is not None:
            rows.append((collection_name, record))
    return rows


def action_generation_status(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
) -> str:
    """Return legacy/new/same/new_action/new_generation for API identity."""

    if action_id is None:
        return "legacy"
    rows = _generation_records(state, key)
    if not rows:
        return "new"
    same_action = [
        (name, record) for name, record in rows
        if _record_action_id(record) == action_id
    ]
    if not same_action:
        return "new_action"

    current_start = _parse_datetime(server_start_at)
    if current_start is None:
        return "same"
    stored_starts = [
        parsed for _, record in same_action
        if (parsed := _parse_datetime(record.get("server_start_at"))) is not None
    ]
    if any(parsed == current_start for parsed in stored_starts):
        return "same"
    if stored_starts:
        return "new_generation" if current_start > max(stored_starts) else "same"

    terminal_markers: list[datetime] = []
    for collection_name, record in same_action:
        terminal = (
            collection_name in {"inactive_wheels", "recently_completed_wheels"}
            or str(record.get("state") or "") in {"closed", "finished", "inactive"}
            or str(record.get("lifecycle_state") or "") in {"finished", "inactive"}
        )
        if not terminal:
            continue
        marker_time = _record_time(
            record,
            "closed_at",
            "removed_at",
            "confirmed_finished_at",
            "marked_at",
            "seen_at",
        )
        if marker_time is not None:
            terminal_markers.append(marker_time)
    if terminal_markers and current_start > max(terminal_markers):
        return "new_generation"
    return "same"


def record_generation_identity(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
    *,
    current: datetime,
    status: str = "active",
) -> str:
    record_generation_observation(
        state,
        key,
        action_id,
        server_start_at,
        current=current,
        status=status,
    )
    if action_id is None:
        return ""
    normalized = str(key or "").casefold()
    start = _parse_datetime(server_start_at)
    identity = generation_id(normalized, action_id, start)
    active = state.get("active_wheels")
    entry = active.get(normalized) if isinstance(active, dict) else None
    if isinstance(entry, dict):
        entry["action_id"] = int(action_id)
        if start is not None:
            entry["server_start_at"] = start.isoformat()
        if identity:
            entry["generation_id"] = identity

    history = {
        "action_id": int(action_id),
        "seen_at": current.astimezone(UTC).isoformat(),
        "state": status,
    }
    if start is not None:
        history["server_start_at"] = start.isoformat()
    if identity:
        history["generation_id"] = identity
    if status in {"closed", "finished", "inactive"}:
        history["closed_at"] = current.astimezone(UTC).isoformat()
    state.setdefault("wheel_action_history", {})[normalized] = history
    return identity


def reset_changed_generation_state(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
) -> list[str]:
    if action_id is None:
        return []
    status = action_generation_status(state, key, action_id, server_start_at)
    if status not in {"new_action", "new_generation"}:
        return []
    normalized = str(key or "").casefold()
    removed: list[str] = []
    for collection_name in (
        "active_wheels",
        "inactive_wheels",
        "recently_completed_wheels",
        "completed_wheel_alerts",
        "url_alerts",
        "activation_alerts",
        "manual_deadlines",
        "manual_overrides",
        "participating_wheels",
        "wheel_publications",
    ):
        collection = state.get(collection_name)
        if isinstance(collection, dict) and normalized in collection:
            collection.pop(normalized, None)
            removed.append(collection_name)
    return removed


def known_action_ids(state: dict[str, Any], key: str) -> set[int]:
    """Return authoritative BetBoom identities already seen for one URL."""

    normalized = str(key or "").casefold()
    known_ids: set[int] = set()
    for collection_name in (
        "active_wheels",
        "inactive_wheels",
        "recently_completed_wheels",
        "wheel_action_history",
    ):
        collection = state.get(collection_name)
        record = collection.get(normalized) if isinstance(collection, dict) else None
        if not isinstance(record, dict):
            continue
        try:
            known = int(record.get("action_id"))
        except (TypeError, ValueError):
            continue
        if known > 0:
            known_ids.add(known)
    return known_ids


def reset_changed_action_state(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
) -> list[str]:
    """Compatibility wrapper: a changed action is also a changed generation."""

    return reset_changed_generation_state(state, key, action_id, None)


def recover_recent_events_from_seen(
    state: dict[str, Any],
    stats: dict[str, Any],
    *,
    current: datetime,
    recovery_window: timedelta = EVENT_REUSE_GAP,
) -> list[str]:
    """Requeue recent posts that an older event marker suppressed before this fix."""

    seen = state.get("seen")
    sources = stats.get("sources") if isinstance(stats, dict) else None
    if not isinstance(seen, dict) or not isinstance(sources, dict):
        return []
    current = current.astimezone(UTC)
    recovered: list[str] = []
    for source_row in sources.values():
        if not isinstance(source_row, dict):
            continue
        recent = source_row.get("recent_post_keys")
        if not isinstance(recent, dict):
            continue
        for post_key, record in recent.items():
            if post_key not in seen or not isinstance(record, dict):
                continue
            wheel = str(record.get("wheel") or "").casefold()
            event_at = _parse_datetime(record.get("seen_at"))
            if not wheel or event_at is None or current - event_at > recovery_window:
                continue
            if wheel in state.get("active_wheels", {}):
                continue
            closed_markers = [
                _record_time(state.get(name, {}).get(wheel), *fields)
                for name, fields in (
                    ("inactive_wheels", ("marked_at",)),
                    ("recently_completed_wheels", ("removed_at", "confirmed_finished_at")),
                    ("manual_deadlines", ("updated_at",)),
                )
                if isinstance(state.get(name), dict)
            ]
            newest_close = max(
                (value for value in closed_markers if value is not None),
                default=None,
            )
            if newest_close is not None and newest_close >= event_at:
                continue
            reset_stale_event_state(state, wheel, event_at)
            seen.pop(post_key, None)
            recovered.append(post_key)
    return recovered


def _availability_for_message(
    monitor_module: Any,
    original_deadline_parser: Callable,
    message: Any,
) -> tuple[datetime | None, str]:
    return infer_availability(
        str(getattr(message, "text", "") or ""),
        getattr(message, "date"),
        original_deadline_parser,
    )


def _tag_availability(
    monitor_module: Any,
    original_deadline_parser: Callable,
    state: dict[str, Any],
    message: Any,
    link: str,
    *,
    available_at: datetime | None = None,
    method: str = "",
) -> None:
    if available_at is None:
        available_at, inferred_method = _availability_for_message(
            monitor_module, original_deadline_parser, message
        )
        method = method or inferred_method
    if available_at is None:
        return
    key = monitor_module.wheel_key(link)
    entry = state.setdefault("active_wheels", {}).get(key)
    if not isinstance(entry, dict):
        return
    current = monitor_module.now_utc()
    entry["available_at"] = available_at.isoformat()
    entry["availability_method"] = method[:300]
    entry["expires_at"] = (current + ACTIVE_WITHOUT_DRAW_TTL).isoformat()
    deadline = monitor_module.parse_datetime(entry.get("deadline"))
    if available_at > current:
        entry["status"] = "scheduled_availability"
        entry["availability_status"] = "scheduled"
        entry["needs_manual_time"] = deadline is None
        entry.pop("availability_notified_at", None)
    else:
        entry["status"] = "available"
        entry["availability_status"] = "available"
        entry["needs_manual_time"] = deadline is None
        entry.setdefault("availability_notified_at", current.isoformat())


def _availability_message(
    monitor_module: Any,
    state: dict[str, Any],
    message: Any,
    link: str,
    available_at: datetime,
    method: str,
    deadline: datetime | None = None,
    *,
    action_id: int | None = None,
    verification_status: str = "",
    server_start_at: datetime | None = None,
) -> None:
    current = monitor_module.now_utc()
    future = available_at > current
    identifier = html.escape(monitor_module.wheel_identifier(link))
    published = message.date.astimezone(monitor_module.DISPLAY_TZ)
    if future:
        title = "🟡 <b>Новое колесо BetBoom — участие откроется позже</b>"
        timing = (
            "🕒 Будет доступно через: "
            f"<b>{html.escape(monitor_module.human_remaining(available_at))}</b>"
        )
        if deadline is not None:
            timing += (
                "\n⏳ До прокрутки: "
                f"<b>{html.escape(monitor_module.human_remaining(deadline))}</b>"
            )
        status = "scheduled_availability"
    else:
        title = "🟢 <b>Колесо BetBoom доступно для участия</b>"
        timing = (
            "✅ Можно участвовать сейчас\n"
            + (
                "⏳ До прокрутки: "
                f"<b>{html.escape(monitor_module.human_remaining(deadline))}</b>"
                if deadline is not None
                else "🔴 <b>Время прокрутки неизвестно</b>"
            )
        )
        status = "available"
    verification = (
        "\n🟡 <b>Проверка активности временно недоступна</b>"
        if verification_status == monitor_module.WHEEL_VERIFICATION_FAILED
        else ""
    )
    monitor_module.send_message(
        f"{title}\n\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\n"
        f"Идентификатор: <code>{identifier}</code>\n"
        f"Пост: {published:%d.%m.%Y %H:%M}\n"
        f"{timing}{verification}",
        reply_markup=monitor_module.wheel_reply_markup(
            state,
            message,
            link,
            active=not future,
            status=status,
            method=method,
        ),
    )
    monitor_module.remember_active_wheel(
        state,
        message,
        link,
        deadline,
        status,
        method,
        "",
        action_id=action_id,
        available_at=available_at,
        verification_status=verification_status,
        server_start_at=server_start_at,
    )
    _tag_availability(
        monitor_module,
        monitor_module._bbvg_original_deadline_parser,
        state,
        message,
        link,
        available_at=available_at,
        method=method,
    )


def process_due_availability(monitor_module: Any, state: dict[str, Any]) -> dict[str, Any]:
    current = monitor_module.now_utc()
    changed = False
    sent = 0
    for key, entry in list(state.setdefault("active_wheels", {}).items()):
        if not isinstance(entry, dict):
            continue
        available_at = monitor_module.parse_datetime(entry.get("available_at"))
        if available_at is None or available_at > current:
            continue
        if monitor_module.parse_datetime(entry.get("availability_notified_at")):
            continue
        message = monitor_module.active_entry_message(entry)
        url = str(entry.get("url") or "")
        if message is None or not url:
            continue
        sources = entry.get("sources") if isinstance(entry.get("sources"), list) else []
        source_text = ", ".join(f"@{html.escape(str(value).lstrip('@'))}" for value in sources)
        if not source_text:
            source_text = f"@{html.escape(str(entry.get('source') or 'неизвестно'))}"
        deadline = monitor_module.parse_datetime(entry.get("deadline"))
        timing = (
            "⏳ До прокрутки: "
            f"<b>{html.escape(monitor_module.human_remaining(deadline))}</b>"
            if deadline is not None
            else "🔴 <b>Время прокрутки неизвестно</b>"
        )
        verification = (
            "\n🟡 <b>Проверка активности временно недоступна</b>"
            if entry.get("verification_status")
            == getattr(monitor_module, "WHEEL_VERIFICATION_FAILED", "failed")
            else ""
        )
        monitor_module.send_message(
            "🟢 <b>Колесо BetBoom доступно для участия</b>\n\n"
            f"Идентификатор: <code>{html.escape(str(entry.get('identifier') or key))}</code>\n"
            f"Источники: {source_text}\n"
            "✅ Теперь можно принять участие.\n"
            f"{timing}{verification}",
            reply_markup=monitor_module.wheel_reply_markup(
                state,
                message,
                url,
                active=True,
                status="available",
                method=str(entry.get("availability_method") or "время открытия наступило"),
                page_excerpt=str(entry.get("page_excerpt") or ""),
            ),
        )
        entry["availability_notified_at"] = current.isoformat()
        entry["availability_status"] = "available"
        entry["status"] = "available"
        entry["needs_manual_time"] = deadline is None
        entry["last_notification_at"] = current.isoformat()
        entry["expires_at"] = (
            monitor_module.participation_expiry(deadline, current=current).isoformat()
            if deadline is not None
            else (current + ACTIVE_WITHOUT_DRAW_TTL).isoformat()
        )
        wheel_lifecycle_v2.stamp_lifecycle(str(key).casefold(), entry, current)
        sent += 1
        changed = True
    return {"changed": changed, "availability_notifications": sent}


def install(monitor_module: Any, runtime_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_wheel_event_runtime_installed", False):
        return

    original_deadline_parser: Callable = monitor_module.infer_deadline
    original_assess_new: Callable = monitor_module.assess_new_wheel
    original_assess_pending: Callable = monitor_module.assess_pending_wheel
    original_notify_new: Callable = monitor_module.notify_new_link
    original_notify_activation: Callable = monitor_module.notify_activation
    original_remember_active: Callable = monitor_module.remember_active_wheel
    original_remember_pending: Callable = runtime_module.remember_without_pending
    original_process_active: Callable = monitor_module.process_active_wheels
    original_load_state: Callable = monitor_module.load_state

    monitor_module._bbvg_original_deadline_parser = original_deadline_parser

    def infer_draw_deadline(text: str, published_at: datetime):
        available_at, method = infer_availability(text, published_at, original_deadline_parser)
        if available_at is not None:
            return None, method
        return original_deadline_parser(text, published_at)

    def legacy_prepare_event(message: Any, link: str, state: Any) -> set[int]:
        if not isinstance(state, dict):
            return set()
        key = monitor_module.wheel_key(link)
        known = known_action_ids(state, key)
        if not known:
            reset_stale_event_state(state, key, message.date.astimezone(UTC))
        return known

    def apply_action_identity(
        link: str,
        state: Any,
        result: Any,
        known_before: set[int],
    ):
        action_id = result.action_id
        if not isinstance(state, dict):
            return result
        key = monitor_module.wheel_key(link)
        if action_id is None or result.status in {"inactive", "not_started"}:
            record_generation_observation(
                state,
                key,
                action_id,
                result.server_start_at,
                current=monitor_module.now_utc(),
                status=result.status,
            )
        if action_id is None:
            return result
        generation_status = action_generation_status(
            state, key, action_id, result.server_start_at
        )
        if action_id in known_before and generation_status == "same":
            if result.status == "inactive":
                return result
            return monitor_module.WheelAssessment(
                False,
                result.deadline,
                "это поколение колеса BetBoom уже было обработано",
                "duplicate_action",
                result.page_excerpt,
                action_id=action_id,
                available_at=result.available_at,
                verification_status=result.verification_status,
                server_start_at=result.server_start_at,
            )
        if generation_status in {"new_action", "new_generation"}:
            reset_changed_generation_state(
                state, key, action_id, result.server_start_at
            )
        return result

    def assessment_availability(message: Any, result: Any):
        text_available, text_method = _availability_for_message(
            monitor_module, original_deadline_parser, message
        )
        candidates = [
            value
            for value in (result.available_at, text_available)
            if value is not None
        ]
        available_at = max(candidates) if candidates else None
        method = text_method if text_available == available_at else result.method
        return available_at, method

    def assess_new(message: Any, link: str, state: Any = None):
        known_before = legacy_prepare_event(message, link, state)
        result = original_assess_new(message, link, state)
        result = apply_action_identity(link, state, result, known_before)
        if result.status == "inactive":
            return result
        if result.status == "duplicate_action":
            return result
        available_at, method = assessment_availability(message, result)
        if available_at is None or available_at <= monitor_module.now_utc():
            return result
        return monitor_module.WheelAssessment(
            True,
            result.deadline,
            method or result.method,
            "scheduled_availability",
            result.page_excerpt,
            action_id=result.action_id,
            available_at=available_at,
            verification_status=result.verification_status,
            server_start_at=result.server_start_at,
        )

    def assess_pending(message: Any, link: str, state: Any = None):
        known_before = legacy_prepare_event(message, link, state)
        result = original_assess_pending(message, link, state)
        result = apply_action_identity(link, state, result, known_before)
        if result.status == "inactive":
            return result
        if result.status == "duplicate_action":
            return result
        available_at, method = assessment_availability(message, result)
        if available_at is None or available_at <= monitor_module.now_utc():
            return result
        return monitor_module.WheelAssessment(
            True,
            result.deadline,
            method or result.method,
            "scheduled_availability",
            result.page_excerpt,
            action_id=result.action_id,
            available_at=available_at,
            verification_status=result.verification_status,
            server_start_at=result.server_start_at,
        )

    def remember_active(
        state,
        message,
        link,
        deadline,
        status,
        method,
        page_excerpt="",
        *,
        action_id=None,
        available_at=None,
        verification_status="",
        server_start_at=None,
    ):
        original_remember_active(
            state,
            message,
            link,
            deadline,
            status,
            method,
            page_excerpt,
            action_id=action_id,
            available_at=available_at,
            verification_status=verification_status,
            server_start_at=server_start_at,
        )
        _tag_availability(
            monitor_module,
            original_deadline_parser,
            state,
            message,
            link,
            available_at=available_at,
            method=method,
        )
        record_generation_identity(
            state,
            monitor_module.wheel_key(link),
            action_id,
            server_start_at,
            current=monitor_module.now_utc(),
            status="active",
        )

    def remember_pending(
        state,
        post_key,
        message,
        link,
        status,
        reason,
        *,
        initial_notified=False,
    ):
        original_remember_pending(
            state,
            post_key,
            message,
            link,
            status,
            reason,
            initial_notified=initial_notified,
        )
        _tag_availability(
            monitor_module, original_deadline_parser, state, message, link
        )

    def notify_new(
        message,
        link,
        deadline,
        method,
        mappings,
        state=None,
        page_excerpt="",
        *,
        action_id=None,
        available_at=None,
        verification_status="",
        server_start_at=None,
    ):
        inferred_at, availability_method = _availability_for_message(
            monitor_module, original_deadline_parser, message
        )
        candidates = [value for value in (available_at, inferred_at) if value is not None]
        available_at = max(candidates) if candidates else None
        if available_at is None or not isinstance(state, dict):
            return original_notify_new(
                message,
                link,
                deadline,
                method,
                mappings,
                state,
                page_excerpt,
                action_id=action_id,
                available_at=available_at,
                verification_status=verification_status,
                server_start_at=server_start_at,
            )
        return _availability_message(
            monitor_module,
            state,
            message,
            link,
            available_at,
            availability_method or method,
            deadline,
            action_id=action_id,
            verification_status=verification_status,
            server_start_at=server_start_at,
        )

    def notify_activation(
        message,
        link,
        deadline,
        method,
        mappings,
        state=None,
        page_excerpt="",
        *,
        action_id=None,
        available_at=None,
        verification_status="",
        server_start_at=None,
    ):
        inferred_at, availability_method = _availability_for_message(
            monitor_module, original_deadline_parser, message
        )
        candidates = [value for value in (available_at, inferred_at) if value is not None]
        available_at = max(candidates) if candidates else None
        if available_at is None or not isinstance(state, dict):
            return original_notify_activation(
                message,
                link,
                deadline,
                method,
                mappings,
                state,
                page_excerpt,
                action_id=action_id,
                available_at=available_at,
                verification_status=verification_status,
                server_start_at=server_start_at,
            )
        return _availability_message(
            monitor_module,
            state,
            message,
            link,
            available_at,
            availability_method or method,
            deadline,
            action_id=action_id,
            verification_status=verification_status,
            server_start_at=server_start_at,
        )

    def process_active(state: dict, stats: dict):
        availability = process_due_availability(monitor_module, state)
        result = original_process_active(state, stats)
        result["availability_notifications"] = int(
            availability.get("availability_notifications", 0) or 0
        )
        if availability.get("changed"):
            result["changed"] = True
        return result

    def load_state_with_event_recovery():
        state = original_load_state()
        try:
            stats = monitor_module.data_store.load_stats()
            recover_recent_events_from_seen(
                state,
                stats,
                current=monitor_module.now_utc(),
            )
        except Exception as exc:
            print(
                "WARNING recurring-event recovery was skipped: "
                f"{type(exc).__name__}: {exc}"
            )
        return state

    monitor_module.infer_deadline = infer_draw_deadline
    monitor_module.infer_availability = lambda text, published_at: infer_availability(
        text, published_at, original_deadline_parser
    )
    monitor_module.assess_new_wheel = assess_new
    monitor_module.assess_pending_wheel = assess_pending
    monitor_module.notify_new_link = notify_new
    monitor_module.notify_activation = notify_activation
    monitor_module.remember_active_wheel = remember_active
    runtime_module.remember_without_pending = remember_pending
    monitor_module.remember_pending = remember_pending
    monitor_module.process_active_wheels = process_active
    monitor_module.load_state = load_state_with_event_recovery
    monitor_module._bbvg_wheel_event_runtime_installed = True


def self_test() -> None:
    published = datetime(2026, 7, 15, 12, 17, tzinfo=UTC)

    def parser(text: str, at: datetime):
        return at + timedelta(hours=2), "относительное время"

    available_at, _ = infer_availability(
        "Через 2 часа запущу колесо с фрибетами", published, parser
    )
    assert available_at == published + timedelta(hours=2)
    assert infer_availability("Итоги через 2 часа", published, parser)[0] is None

    reused = {
        "active_wheels": {"same": {"action_id": 100}},
        "participating_wheels": {"same": {"marked_at": published.isoformat()}},
        "wheel_publications": {"same": [{"source": "old"}]},
    }
    removed = reset_changed_action_state(reused, "same", 101)
    assert "active_wheels" in removed
    assert "same" not in reused["active_wheels"]
    assert "same" not in reused["participating_wheels"]
    assert "same" not in reused["wheel_publications"]

    state = {
        "inactive_wheels": {
            "risen": {"marked_at": "2026-07-14T12:00:00+00:00"}
        },
        "manual_deadlines": {
            "risen": {"updated_at": "2026-07-14T12:01:00+00:00"}
        },
        "recently_completed_wheels": {
            "risen": {"removed_at": "2026-07-14T14:00:00+00:00"}
        },
    }
    removed = reset_stale_event_state(state, "risen", published)
    assert {"inactive_wheels", "manual_deadlines", "recently_completed_wheels"} <= set(removed)

    observations: dict[str, Any] = {}
    record_generation_observation(
        observations,
        "same",
        100,
        published,
        current=published,
        status="active",
    )
    record_generation_observation(
        observations,
        "same",
        100,
        published + timedelta(hours=6),
        current=published + timedelta(hours=6),
        status="active",
    )
    report = generation_observation_report(
        observations, current=published + timedelta(hours=6)
    )
    assert report["same_action_id_multiple_starts"] == [
        {
            "wheel_key": "same",
            "action_id": 100,
            "server_start_at": [
                published.isoformat(),
                (published + timedelta(hours=6)).isoformat(),
            ],
        }
    ]
    print("recurring wheel event and availability self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BB V.G. recurring wheel identity diagnostics"
    )
    parser.add_argument(
        "--observation-report",
        type=Path,
        metavar="STATE_JSON",
        help="print a JSON report from the bounded generation history",
    )
    args = parser.parse_args()
    if args.observation_report is None:
        self_test()
        return 0
    state = json.loads(args.observation_report.read_text(encoding="utf-8"))
    print(json.dumps(generation_observation_report(state), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
