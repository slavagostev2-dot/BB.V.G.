from __future__ import annotations

import hashlib
import html
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import personal_reminder_filter
import wheel_publications_v2


FINAL_REMINDER_BEFORE_MINUTES = max(
    1, int(os.getenv("FINAL_REMINDER_BEFORE_MINUTES", "5"))
)
ACTIVE_REMOVE_GRACE_MINUTES = max(
    0, int(os.getenv("ACTIVE_REMOVE_GRACE_MINUTES", "0"))
)
UTC = timezone.utc

LIFECYCLE_TRANSITIONS = (
    ("detected", "future_availability", "scheduled_availability"),
    ("detected", "known_draw_time", "scheduled_draw"),
    ("detected", "unknown_draw_time", "active_unknown_time"),
    ("scheduled_availability", "availability_reached", "active_unknown_time"),
    ("active_unknown_time", "manual_time_set", "scheduled_draw"),
    ("scheduled_draw", "manual_time_changed", "scheduled_draw"),
    ("scheduled_availability", "participate", "participating"),
    ("active_unknown_time", "participate", "participating"),
    ("scheduled_draw", "participate", "participating"),
    ("participating", "draw_time_changed", "participating"),
    ("scheduled_draw", "deadline_reached", "finished"),
    ("participating", "deadline_reached", "finished"),
    ("scheduled_availability", "admin_inactive", "inactive"),
    ("active_unknown_time", "admin_inactive", "inactive"),
    ("scheduled_draw", "admin_inactive", "inactive"),
    ("participating", "admin_inactive", "inactive"),
    ("scheduled_availability", "admin_finished", "finished"),
    ("active_unknown_time", "admin_finished", "finished"),
    ("scheduled_draw", "admin_finished", "finished"),
    ("participating", "admin_finished", "finished"),
)
TERMINAL_STATES = {"finished", "inactive"}
_CLEANUP_COLLECTIONS = (
    "active_wheels",
    "participating_wheels",
    "pending_posts",
    "button_contexts",
    "completed_wheel_alerts",
    "manual_deadlines",
    "manual_overrides",
    "wheel_publications",
)


def wheel_event_id(key: str, entry: dict[str, Any] | None) -> str:
    """Stable identity for one publication event even when its URL is reused."""

    record = entry if isinstance(entry, dict) else {}
    existing = str(record.get("event_id") or "").strip().casefold()
    if existing:
        return existing[:64]
    raw = "\x1f".join(
        (
            str(key or "").casefold(),
            str(record.get("source") or "").casefold(),
            str(record.get("message_id") or ""),
            str(record.get("message_date") or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def rating_event_key(key: str, entry: dict[str, Any] | None) -> str:
    """Use a publication-specific key so a reused wheel counts again."""

    normalized = str(key or "").casefold()
    return f"{normalized}#{wheel_event_id(normalized, entry)}"


def lifecycle_state(
    entry: dict[str, Any], current: datetime | None = None
) -> str:
    current = current or datetime.now(UTC)
    stored = str(entry.get("lifecycle_state") or "")
    if stored in TERMINAL_STATES:
        return stored
    if bool(entry.get("participating")):
        return "participating"
    available_at = monitor_datetime(entry.get("available_at"))
    if available_at is not None and available_at > current:
        return "scheduled_availability"
    if monitor_datetime(entry.get("deadline")) is not None:
        return "scheduled_draw"
    return "active_unknown_time"


def stamp_lifecycle(
    key: str,
    entry: dict[str, Any],
    current: datetime | None = None,
) -> bool:
    current = current or datetime.now(UTC)
    changed = False
    event_id = str(entry.get("event_id") or wheel_event_id(key, entry))
    state_name = lifecycle_state(entry, current)
    if entry.get("event_id") != event_id:
        entry["event_id"] = event_id
        changed = True
    if entry.get("lifecycle_state") != state_name:
        entry["lifecycle_state"] = state_name
        entry["lifecycle_changed_at"] = current.isoformat()
        changed = True
    return changed


def _record_matches(key: str, record_key: Any, record: Any) -> bool:
    normalized = str(key or "").casefold()
    direct = str(record_key or "").casefold()
    if direct == normalized:
        return True
    if not isinstance(record, dict):
        return False
    related = str(
        record.get("wheel_key")
        or record.get("identifier")
        or ""
    ).casefold()
    if related == normalized:
        return True
    url = str(record.get("url") or "")
    return bool(
        url
        and url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0].casefold()
        == normalized
    )


def cleanup_event_records(state: dict[str, Any], key: str) -> int:
    """Remove every mutable record owned by the terminal current event."""

    removed = 0
    for collection_name in _CLEANUP_COLLECTIONS:
        collection = state.setdefault(collection_name, {})
        if not isinstance(collection, dict):
            state[collection_name] = {}
            continue
        for record_key in list(collection):
            if _record_matches(key, record_key, collection.get(record_key)):
                collection.pop(record_key, None)
                removed += 1
    return removed


def _remember_history(
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any],
    terminal_state: str,
    reason: str,
    current: datetime,
) -> None:
    event_id = str(entry.get("event_id") or wheel_event_id(key, entry))
    history = state.setdefault("wheel_lifecycle_history", {})
    history[event_id] = {
        "event_id": event_id,
        "wheel_key": str(key).casefold(),
        "identifier": str(entry.get("identifier") or key),
        "message_date": str(entry.get("message_date") or ""),
        "state": terminal_state,
        "reason": reason,
        "changed_at": current.isoformat(),
    }
    if len(history) > 1000:
        ordered = sorted(
            history.items(),
            key=lambda item: str((item[1] or {}).get("changed_at") or ""),
        )
        for old_key, _ in ordered[: len(history) - 1000]:
            history.pop(old_key, None)


def complete_event(
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any],
    *,
    current: datetime,
    reason: str,
    deadline: datetime | None = None,
) -> int:
    """Archive one finished event and remove all of its mutable state."""

    normalized = str(key or "").casefold()
    stamp_lifecycle(normalized, entry, current)
    effective_deadline = (
        deadline or monitor_datetime(entry.get("deadline")) or current
    )
    _remember_completed(state, normalized, entry, effective_deadline, current)
    recent = state.setdefault("recently_completed_wheels", {}).get(normalized)
    if isinstance(recent, dict):
        recent["event_id"] = str(
            entry.get("event_id") or wheel_event_id(normalized, entry)
        )
        recent["lifecycle_state"] = "finished"
        recent["completion_reason"] = reason
    _remember_history(state, normalized, entry, "finished", reason, current)
    return cleanup_event_records(state, normalized)


def mark_inactive_event(
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any] | None,
    *,
    current: datetime,
    actor: str,
    retention: timedelta = timedelta(days=30),
) -> int:
    """Archive an administrator's inactive verdict for the current event."""

    normalized = str(key or "").casefold()
    record = dict(entry or {"identifier": normalized})
    stamp_lifecycle(normalized, record, current)
    _remember_history(
        state,
        normalized,
        record,
        "inactive",
        "admin_inactive",
        current,
    )
    removed = cleanup_event_records(state, normalized)
    state.setdefault("inactive_wheels", {})[normalized] = {
        "identifier": str(record.get("identifier") or normalized),
        "event_id": str(record.get("event_id") or wheel_event_id(normalized, record)),
        **(
            {"action_id": int(record["action_id"])}
            if str(record.get("action_id") or "").isdigit()
            else {}
        ),
        "lifecycle_state": "inactive",
        "marked_at": current.isoformat(),
        "marked_by": "admin",
        "expires_at": (current + retention).isoformat(),
    }
    return removed


def final_reminder_due(
    monitor_module: Any,
    entry: dict[str, Any],
    current: datetime | None = None,
) -> bool:
    current = current or monitor_module.now_utc()
    if monitor_module.parse_datetime(entry.get("final_reminder_sent_at")):
        return False
    deadline = monitor_module.parse_datetime(entry.get("deadline"))
    if deadline is None:
        return False
    return (
        deadline - timedelta(minutes=FINAL_REMINDER_BEFORE_MINUTES)
        <= current
        < deadline
    )


def _source_text(state: dict[str, Any], key: str, entry: dict[str, Any]) -> str:
    sources = wheel_publications_v2.publication_sources(state, key, entry)
    if not sources:
        return "неизвестно"
    return ", ".join(f"@{html.escape(source)}" for source in sources)


def _remember_completed(
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any],
    deadline: datetime,
    current: datetime,
) -> None:
    recent = state.setdefault("recently_completed_wheels", {})
    recent[key] = {
        "identifier": str(entry.get("identifier") or key),
        "url": str(entry.get("url") or ""),
        "sources": wheel_publications_v2.publication_sources(state, key, entry),
        "deadline": deadline.isoformat(),
        "removed_at": current.isoformat(),
        "expires_at": (current + timedelta(days=1)).isoformat(),
        **(
            {"action_id": int(entry["action_id"])}
            if str(entry.get("action_id") or "").isdigit()
            else {}
        ),
    }
    for old_key, raw in list(recent.items()):
        if not isinstance(raw, dict):
            recent.pop(old_key, None)
            continue
        expires = monitor_datetime(raw.get("expires_at"))
        if expires is not None and expires <= current:
            recent.pop(old_key, None)


def monitor_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def install(monitor_module: Any) -> None:
    """Install the user-facing final reminder and strict active-wheel lifetime."""

    if getattr(monitor_module, "_bbvg_wheel_lifecycle_v2_installed", False):
        return

    original_process: Callable = monitor_module.process_active_wheels

    def process_active_with_final_reminder(state: dict, stats: dict):
        current = monitor_module.now_utc()
        changed = False
        final_sent = 0

        for key, entry in list(state.setdefault("active_wheels", {}).items()):
            if not isinstance(entry, dict):
                continue
            normalized = str(key).casefold()
            if stamp_lifecycle(normalized, entry, current):
                changed = True
            global_participating = monitor_module.is_participating(state, normalized)
            personal_reminder_filter.set_global_participating(
                normalized, global_participating
            )
            if global_participating and entry.get("lifecycle_state") != "participating":
                entry["lifecycle_state"] = "participating"
                entry["lifecycle_changed_at"] = current.isoformat()
                changed = True
            if not final_reminder_due(monitor_module, entry, current):
                continue
            deadline = monitor_module.parse_datetime(entry.get("deadline"))
            message = monitor_module.active_entry_message(entry)
            url = str(entry.get("url") or "")
            if deadline is None or message is None or not url:
                continue
            try:
                monitor_module.send_message(
                    "🚨 <b>Напоминание о колесе BetBoom: последний шанс</b>\n\n"
                    f"Идентификатор: <code>{html.escape(str(entry.get('identifier') or normalized))}</code>\n"
                    f"Источники: {_source_text(state, normalized, entry)}\n"
                    f"⏳ Осталось: <b>{html.escape(monitor_module.human_remaining(deadline))}</b>\n\n"
                    "Вы ещё не отметили участие. Откройте колесо сейчас — оно скоро завершится.",
                    reply_markup=monitor_module.wheel_reply_markup(
                        state,
                        message,
                        url,
                        active=True,
                        status="final_reminder",
                        method=str(entry.get("method") or "final reminder"),
                        page_excerpt=str(entry.get("page_excerpt") or ""),
                    ),
                )
            except Exception as exc:
                entry["final_reminder_error"] = f"{type(exc).__name__}: {exc}"[:300]
            else:
                entry["final_reminder_sent_at"] = current.isoformat()
                entry["known_reminder_sent_at"] = current.isoformat()
                entry["last_notification_at"] = current.isoformat()
                sources = wheel_publications_v2.publication_sources(
                    state, normalized, entry
                ) or [str(entry.get("source") or "unknown")]
                for source in sources:
                    monitor_module.data_store.increment_stat(
                        stats, source, "final_participation_reminders"
                    )
                final_sent += 1
                changed = True

        result = original_process(state, stats)
        current = monitor_module.now_utc()
        for key, entry in list(state.setdefault("active_wheels", {}).items()):
            if not isinstance(entry, dict):
                continue
            deadline = monitor_module.parse_datetime(entry.get("deadline"))
            if deadline is None:
                continue
            remove_at = deadline + timedelta(minutes=ACTIVE_REMOVE_GRACE_MINUTES)
            if current < remove_at:
                continue
            normalized = str(key).casefold()
            complete_event(
                state,
                normalized,
                entry,
                current=current,
                reason="deadline_reached",
                deadline=deadline,
            )
            personal_reminder_filter.set_global_participating(normalized, False)
            result["removed"] = int(result.get("removed", 0) or 0) + 1
            changed = True

        result["final_reminders"] = final_sent
        if changed:
            result["changed"] = True
        return result

    monitor_module.process_active_wheels = process_active_with_final_reminder
    monitor_module._bbvg_wheel_lifecycle_v2_installed = True


def self_test() -> None:
    class FakeMonitor:
        @staticmethod
        def now_utc() -> datetime:
            return datetime(2026, 7, 14, 12, 56, tzinfo=UTC)

        parse_datetime = staticmethod(monitor_datetime)

    entry = {"deadline": "2026-07-14T13:00:00+00:00"}
    assert final_reminder_due(FakeMonitor, entry)
    entry["final_reminder_sent_at"] = "2026-07-14T12:56:00+00:00"
    assert not final_reminder_due(FakeMonitor, entry)
    assert ACTIVE_REMOVE_GRACE_MINUTES == 0
    assert "Напоминание о колесе BetBoom" in (
        "Напоминание о колесе BetBoom: последний шанс"
    )
    print("wheel lifecycle v2 self-test passed")


if __name__ == "__main__":
    self_test()
