from __future__ import annotations

from datetime import timedelta
from typing import Any

import monitor_entry as base_runtime


monitor = base_runtime.monitor
UNTIMED_WHEEL_TTL_HOURS = monitor.UNTIMED_WHEEL_TTL_HOURS
INACTIVE_TOMBSTONE_DAYS = 30

_original_load_state = monitor.load_state
_original_remember_active_wheel = monitor.remember_active_wheel
_original_remember_pending = monitor.remember_pending
_original_wheel_reply_markup = monitor.wheel_reply_markup
_original_assess_new = monitor.assess_new_wheel
_original_assess_pending = monitor.assess_pending_wheel
_original_process_active_wheels = monitor.process_active_wheels


def _wheel_key_from_record(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    key = str(record.get("wheel_key") or record.get("identifier") or "").casefold()
    if key:
        return key
    url = str(record.get("url") or "")
    if not url:
        return ""
    try:
        return monitor.wheel_key(url)
    except Exception:
        return ""


def _deadline_from_record(record: Any):
    if not isinstance(record, dict):
        return None
    stored = monitor.parse_datetime(record.get("deadline"))
    if stored:
        return stored
    published = monitor.parse_datetime(record.get("message_date"))
    text = str(record.get("message_text") or "")
    if not published or not text:
        return None
    deadline, _ = monitor.infer_deadline(text, published)
    return deadline


def _inactive_entry(state: dict, key: str) -> dict | None:
    rows = state.setdefault("inactive_wheels", {})
    value = rows.get(key.casefold())
    if not isinstance(value, dict):
        return None
    expires = monitor.parse_datetime(value.get("expires_at"))
    if expires and expires <= monitor.now_utc():
        rows.pop(key.casefold(), None)
        return None
    return value


def _record_action_id(record: Any) -> int | None:
    if not isinstance(record, dict):
        return None
    try:
        value = int(record.get("action_id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _inactive_blocks_result(state: dict, key: str, result: Any) -> bool:
    inactive = _inactive_entry(state, key)
    if inactive is None:
        return False
    previous_action = _record_action_id(inactive) or _record_action_id(
        state.get("wheel_action_history", {}).get(key)
        if isinstance(state.get("wheel_action_history"), dict)
        else None
    )
    current_action = _record_action_id({"action_id": result.action_id})
    return not (
        previous_action is not None
        and current_action is not None
        and previous_action != current_action
    )


def _untimed_expiry(current=None, *, available_at=None):
    current = current or monitor.now_utc()
    available_at = monitor.parse_datetime(available_at)
    anchor = max(current, available_at) if available_at is not None else current
    return anchor + timedelta(hours=UNTIMED_WHEEL_TTL_HOURS)


def _entry_untimed_expiry(entry: dict, current=None):
    current = current or monitor.now_utc()
    first_seen = (
        monitor.parse_datetime(entry.get("first_notified_at"))
        or monitor.parse_datetime(entry.get("message_date"))
        or current
    )
    return _untimed_expiry(first_seen, available_at=entry.get("available_at"))


def _pending_to_active(state: dict, record: dict) -> bool:
    key = _wheel_key_from_record(record)
    if not key or _inactive_entry(state, key):
        return False

    active = state.setdefault("active_wheels", {})
    existing = active.get(key)
    current = monitor.now_utc()
    deadline = _deadline_from_record(record)

    if isinstance(existing, dict):
        changed = False
        for field in (
            "identifier",
            "url",
            "source",
            "message_id",
            "message_date",
            "message_url",
            "message_text",
            "method",
            "page_excerpt",
        ):
            value = record.get(field)
            if value not in (None, "") and existing.get(field) in (None, ""):
                existing[field] = value
                changed = True
        if deadline and monitor.parse_datetime(existing.get("deadline")) is None:
            existing["deadline"] = deadline.isoformat()
            existing["expires_at"] = monitor.participation_expiry(
                deadline, current=current
            ).isoformat()
            existing["needs_manual_time"] = False
            changed = True
        elif not deadline and monitor.parse_datetime(existing.get("deadline")) is None:
            existing["needs_manual_time"] = True
            existing["expires_at"] = _entry_untimed_expiry(
                existing, current
            ).isoformat()
            changed = True
        return changed

    first_seen = (
        monitor.parse_datetime(record.get("first_seen_at"))
        or monitor.parse_datetime(record.get("initial_notified_at"))
        or monitor.parse_datetime(record.get("message_date"))
        or current
    )
    expires = (
        monitor.participation_expiry(deadline, current=current)
        if deadline
        else _untimed_expiry(first_seen, available_at=record.get("available_at"))
    )
    active[key] = {
        "identifier": str(record.get("identifier") or key),
        "url": str(record.get("url") or ""),
        "source": str(record.get("source") or "неизвестно"),
        "message_id": int(record.get("message_id", 0) or 0),
        "message_date": str(record.get("message_date") or first_seen.isoformat()),
        "message_url": str(record.get("message_url") or ""),
        "message_text": str(record.get("message_text") or "")[:4000],
        "status": "scheduled" if deadline else "manual_time_required",
        "page_status": str(record.get("status") or "unknown"),
        "method": str(record.get("reason") or record.get("method") or "время не определено")[:300],
        "page_excerpt": str(record.get("page_excerpt") or "")[:1200],
        "first_notified_at": first_seen.isoformat(),
        "last_notification_at": str(record.get("initial_notified_at") or first_seen.isoformat()),
        "last_checked_at": str(record.get("last_checked_at") or current.isoformat()),
        "expires_at": expires.isoformat(),
        "participating": monitor.is_participating(state, key),
        "needs_manual_time": deadline is None,
    }
    if deadline:
        active[key]["deadline"] = deadline.isoformat()
    return True


def load_state_without_pending() -> dict:
    state = _original_load_state()
    state.setdefault("inactive_wheels", {})
    state.setdefault("active_wheels", {})
    state.setdefault("pending_posts", {})

    for key in list(state["inactive_wheels"]):
        _inactive_entry(state, str(key))

    retained_pending: dict[str, dict] = {}
    for post_key, record in list(state["pending_posts"].items()):
        if not isinstance(record, dict):
            continue
        if str(record.get("status") or "") == "not_started":
            retained_pending[str(post_key)] = record
        else:
            _pending_to_active(state, record)
    state["pending_posts"] = retained_pending

    for key in list(state["active_wheels"]):
        if _inactive_entry(state, str(key)):
            state["active_wheels"].pop(key, None)
    return state


def remember_without_pending(
    state: dict,
    post_key: str,
    message,
    link: str,
    status: str,
    reason: str,
    *,
    initial_notified: bool = False,
) -> None:
    key = monitor.wheel_key(link)
    if status == "not_started":
        _original_remember_pending(
            state,
            post_key,
            message,
            link,
            status,
            reason,
            initial_notified=False,
        )
        return
    if status in {
        "inactive",
        "duplicate_action",
        "duplicate_link",
        "duplicate_publication",
    }:
        state.setdefault("pending_posts", {}).pop(post_key, None)
        state.setdefault("seen", {})[post_key] = monitor.now_utc().isoformat()
        return
    if _inactive_entry(state, key):
        state.setdefault("seen", {})[post_key] = monitor.now_utc().isoformat()
        return

    canonical = base_runtime._CANONICAL_MESSAGES.get(key, message)
    base_runtime._persist_publications(state, key, {
        "source": canonical.source,
        "message_id": canonical.message_id,
        "message_date": canonical.date.astimezone(monitor.UTC).isoformat(),
        "message_url": canonical.message_url,
    })
    entry = state.setdefault("active_wheels", {}).get(key)
    if not isinstance(entry, dict):
        deadline, deadline_method = monitor.infer_deadline(canonical.text, canonical.date)
        stored_status = "scheduled" if deadline else "manual_time_required"
        method = deadline_method if deadline else reason
        _original_remember_active_wheel(
            state,
            canonical,
            link,
            deadline,
            stored_status,
            method,
            str(getattr(canonical, "page_excerpt", "") or ""),
        )
        entry = state.setdefault("active_wheels", {}).get(key)

    if isinstance(entry, dict):
        entry["page_status"] = status
        entry["last_checked_at"] = monitor.now_utc().isoformat()
        stored_deadline = monitor.parse_datetime(entry.get("deadline"))
        entry["needs_manual_time"] = stored_deadline is None
        if stored_deadline is None:
            entry["expires_at"] = _entry_untimed_expiry(entry).isoformat()
        else:
            entry["expires_at"] = monitor.participation_expiry(stored_deadline).isoformat()

    state.setdefault("pending_posts", {}).clear()
    if status != "send_error":
        state.setdefault("seen", {})[post_key] = monitor.now_utc().isoformat()


def assess_new_without_pending(message, link, state=None):
    result = _original_assess_new(message, link, state)
    if (
        isinstance(state, dict)
        and _inactive_blocks_result(state, monitor.wheel_key(link), result)
    ):
        return monitor.WheelAssessment(
            False,
            result.deadline,
            "колесо отмечено администратором как неактивное",
            "inactive",
            result.page_excerpt,
            action_id=result.action_id,
            available_at=result.available_at,
            verification_status=result.verification_status,
        )
    return result


def assess_pending_without_pending(message, link, state=None):
    result = _original_assess_pending(message, link, state)
    if (
        isinstance(state, dict)
        and _inactive_blocks_result(state, monitor.wheel_key(link), result)
    ):
        return monitor.WheelAssessment(
            False,
            result.deadline,
            "колесо отмечено администратором как неактивное",
            "inactive",
            result.page_excerpt,
            action_id=result.action_id,
            available_at=result.available_at,
            verification_status=result.verification_status,
        )
    return result


def wheel_reply_markup_bbvg(
    state: dict,
    message,
    link: str,
    *,
    active: bool,
    status: str,
    method: str,
    page_excerpt: str = "",
) -> dict:
    markup = _original_wheel_reply_markup(
        state,
        message,
        link,
        active=active,
        status=status,
        method=method,
        page_excerpt=page_excerpt,
    )
    token = monitor.button_context_token(message, link)
    rows = list(markup.get("inline_keyboard", []))
    open_row = rows[0] if rows else []
    action_row = rows[1] if len(rows) > 1 else []
    post_row = rows[-1] if len(rows) > 2 else []

    participation = next(
        (button for button in action_row if str(button.get("callback_data") or "").startswith(("bb:p:", "bb:n:"))),
        None,
    )
    active_list = next(
        (button for button in action_row if button.get("callback_data") == "bb:l:active"),
        {"text": "📋 Активные колёса", "callback_data": "bb:l:active"},
    )
    inactive = {"text": "🚫 Неактивное", "callback_data": f"bb:x:{token}"}
    deadline, _ = monitor.infer_deadline(message.text, message.date)

    rebuilt: list[list[dict]] = []
    if open_row:
        rebuilt.append(open_row)
    second = [button for button in (participation, inactive) if button]
    if second:
        rebuilt.append(second)
    if deadline is None:
        rebuilt.append([
            {"text": "⏱ Указать время", "callback_data": f"bb:t:{token}"},
            active_list,
        ])
    else:
        rebuilt.append([active_list])
    if post_row and post_row != open_row:
        rebuilt.append(post_row)
    return {"inline_keyboard": rebuilt}


def _manual_deadline(state: dict, key: str, entry: dict):
    manual = state.get("manual_deadlines", {}).get(str(key).casefold())
    if isinstance(manual, dict):
        deadline = monitor.parse_datetime(manual.get("deadline"))
        updated_at = monitor.parse_datetime(manual.get("updated_at"))
        message_at = monitor.parse_datetime(entry.get("message_date"))
        if deadline and (
            message_at is None
            or updated_at is None
            or updated_at >= message_at
        ):
            return deadline
    if str(entry.get("deadline_source") or "") == "manual":
        return monitor.parse_datetime(entry.get("deadline"))
    return None


def _retain_not_started_pending(state: dict) -> int:
    pending = state.setdefault("pending_posts", {})
    if not isinstance(pending, dict):
        state["pending_posts"] = {}
        return 0
    for post_key, record in list(pending.items()):
        if not isinstance(record, dict) or str(record.get("status") or "") != "not_started":
            pending.pop(post_key, None)
    return len(pending)


def _defer_unstarted_active(
    state: dict,
    key: str,
    entry: dict,
    inspection: Any,
    current,
) -> bool:
    message = monitor.active_entry_message(entry)
    url = str(entry.get("url") or "")

    import wheel_lifecycle_v2

    wheel_lifecycle_v2.cleanup_event_records(state, key)
    state.setdefault("wheel_action_history", {}).pop(key, None)
    state.setdefault("activation_alerts", {}).pop(key, None)
    state.setdefault("url_alerts", {}).pop(key, None)

    if message is None or not url:
        return False
    post_key = monitor.notification_key(message, url)
    state.setdefault("seen", {}).pop(post_key, None)
    _original_remember_pending(
        state,
        post_key,
        message,
        url,
        "not_started",
        inspection.method,
        initial_notified=False,
    )
    pending_entry = state.setdefault("pending_posts", {}).get(post_key)
    if isinstance(pending_entry, dict):
        pending_entry["last_checked_at"] = current.isoformat()
        pending_entry["verification_status"] = monitor.WHEEL_VERIFICATION_CONFIRMED
        if inspection.action_id is not None:
            pending_entry["action_id"] = inspection.action_id
    return True


def _start_revalidated_action(
    state: dict,
    active: dict,
    key: str,
    entry: dict,
    action_id: int | None,
    current,
) -> dict:
    previous_action = _record_action_id(entry) or _record_action_id(
        state.get("wheel_action_history", {}).get(str(key).casefold())
        if isinstance(state.get("wheel_action_history"), dict)
        else None
    )
    if (
        previous_action is None
        or action_id is None
        or previous_action == action_id
    ):
        return entry

    # BetBoom reused the URL for a different action. Mutable decisions from
    # the previous event must not leak into the new one, while the source
    # metadata remains useful for the active card.
    import wheel_event_runtime
    import wheel_lifecycle_v2

    preserved = dict(entry)
    wheel_event_runtime.reset_changed_action_state(state, key, action_id)
    for field in (
        "event_id",
        "lifecycle_state",
        "lifecycle_changed_at",
        "participating",
        "participating_at",
        "known_reminder_sent_at",
        "final_reminder_sent_at",
        "last_unknown_reminder_at",
        "manual_time_waiting_since",
        "availability_notified_at",
        "last_reminder_error",
        "final_reminder_error",
    ):
        preserved.pop(field, None)
    preserved["first_notified_at"] = current.isoformat()
    preserved["participating"] = False
    active[key] = preserved
    wheel_lifecycle_v2.stamp_lifecycle(str(key).casefold(), preserved, current)
    return preserved


def revalidate_active_wheels(state: dict, current=None) -> dict[str, int | bool]:
    """Recheck every active wheel through the authoritative BetBoom API."""

    current = current or monitor.now_utc()
    active = state.setdefault("active_wheels", {})
    changed = False
    checked = 0
    confirmed = 0
    failed = 0
    removed = 0
    deferred = 0
    for key, entry in list(active.items()):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        if not url:
            continue
        inspection = monitor.inspect_wheel_page(url)
        checked += 1
        monitor.record_wheel_api_verification(
            state, inspection, checked_at=current
        )
        entry["last_verification_at"] = current.isoformat()
        entry["last_checked_at"] = current.isoformat()
        if inspection.status == "verification_failed":
            entry["verification_status"] = monitor.WHEEL_VERIFICATION_FAILED
            entry["verification_retry_at"] = (
                current + timedelta(minutes=monitor.PENDING_RECHECK_MINUTES)
            ).isoformat()
            entry["last_verification_error"] = inspection.method[:300]
            failed += 1
            changed = True
            continue
        if inspection.status == "not_started":
            _defer_unstarted_active(
                state,
                str(key).casefold(),
                entry,
                inspection,
                current,
            )
            deferred += 1
            changed = True
            continue
        if inspection.status == "inactive":
            if inspection.action_id is not None:
                state.setdefault("wheel_action_history", {})[
                    str(key).casefold()
                ] = {
                    "action_id": inspection.action_id,
                    "seen_at": current.isoformat(),
                }
            import wheel_lifecycle_v2

            wheel_lifecycle_v2.cleanup_event_records(
                state, str(key).casefold()
            )
            removed += 1
            changed = True
            continue

        entry = _start_revalidated_action(
            state,
            active,
            str(key).casefold(),
            entry,
            inspection.action_id,
            current,
        )
        entry["verification_status"] = monitor.WHEEL_VERIFICATION_CONFIRMED
        entry["status"] = (
            "scheduled_availability"
            if inspection.available_at is not None
            and inspection.available_at > current
            else "active"
        )
        entry["page_status"] = "active"
        entry["method"] = inspection.method[:300]
        entry.pop("verification_retry_at", None)
        entry.pop("last_verification_error", None)
        if inspection.action_id is not None:
            entry["action_id"] = inspection.action_id
            state.setdefault("wheel_action_history", {})[str(key).casefold()] = {
                "action_id": inspection.action_id,
                "seen_at": current.isoformat(),
            }
        if inspection.available_at is not None:
            entry["available_at"] = inspection.available_at.isoformat()
            entry["availability_status"] = "scheduled"
        else:
            entry.pop("available_at", None)
            entry["availability_status"] = "available"

        deadline = _manual_deadline(state, str(key), entry)
        deadline_source = "manual" if deadline is not None else "api"
        deadline = deadline or inspection.deadline
        if deadline is not None:
            entry["deadline"] = deadline.isoformat()
            entry["deadline_source"] = deadline_source
            entry["expires_at"] = monitor.participation_expiry(
                deadline, current=current
            ).isoformat()
            participant = state.get("participating_wheels", {}).get(
                str(key).casefold()
            )
            if isinstance(participant, dict):
                participant["deadline"] = deadline.isoformat()
                participant["expires_at"] = entry["expires_at"]
            entry["needs_manual_time"] = False
        else:
            entry.pop("deadline", None)
            entry["deadline_source"] = "api_missing"
            entry["expires_at"] = _entry_untimed_expiry(
                entry, current
            ).isoformat()
            entry["needs_manual_time"] = True
        confirmed += 1
        changed = True
    return {
        "changed": changed,
        "checked": checked,
        "confirmed": confirmed,
        "failed": failed,
        "removed": removed,
        "deferred": deferred,
    }


def retry_unverified_wheels(state: dict, current=None) -> dict[str, int | bool]:
    """Compatibility alias for the full active-wheel revalidation pass."""

    return revalidate_active_wheels(state, current)


def process_active_without_page_verdict(state: dict, stats: dict):
    current = monitor.now_utc()
    verification = revalidate_active_wheels(state, current)
    changed = bool(verification.get("changed"))
    _retain_not_started_pending(state)

    for key, entry in list(state.setdefault("active_wheels", {}).items()):
        if not isinstance(entry, dict):
            state["active_wheels"].pop(key, None)
            changed = True
            continue
        if _inactive_entry(state, str(key)):
            state["active_wheels"].pop(key, None)
            changed = True
            continue
        if monitor.parse_datetime(entry.get("deadline")) is None:
            minimum_expiry = _entry_untimed_expiry(entry, current)
            existing_expiry = monitor.parse_datetime(entry.get("expires_at"))
            if existing_expiry != minimum_expiry:
                entry["expires_at"] = minimum_expiry.isoformat()
                changed = True
            entry["needs_manual_time"] = True

    original_inspector = monitor.inspect_wheel_page
    monitor.inspect_wheel_page = lambda url: monitor.WheelInspection(
        "unknown", None, "состояние определяется пользователями BB V.G."
    )
    try:
        result = _original_process_active_wheels(state, stats)
    finally:
        monitor.inspect_wheel_page = original_inspector

    pending_total = _retain_not_started_pending(state)
    if changed:
        result["changed"] = True
    result["verification_confirmed"] = int(verification.get("confirmed", 0) or 0)
    result["verification_checked"] = int(verification.get("checked", 0) or 0)
    result["verification_failed"] = int(verification.get("failed", 0) or 0)
    result["verification_removed"] = int(verification.get("removed", 0) or 0)
    result["verification_deferred"] = int(verification.get("deferred", 0) or 0)
    result["pending_total"] = pending_total
    return result


monitor.load_state = load_state_without_pending
monitor.remember_pending = remember_without_pending
monitor.assess_new_wheel = assess_new_without_pending
monitor.assess_pending_wheel = assess_pending_without_pending
monitor.wheel_reply_markup = wheel_reply_markup_bbvg
monitor.process_active_wheels = process_active_without_page_verdict


if __name__ == "__main__":
    raise SystemExit(monitor.main())
