from __future__ import annotations

from datetime import timedelta
from typing import Any

import monitor_entry as base_runtime


monitor = base_runtime.monitor
UNTIMED_WHEEL_TTL_HOURS = monitor.UNTIMED_WHEEL_TTL_HOURS
INACTIVE_TOMBSTONE_DAYS = 30

_original_load_state = monitor.load_state
_original_remember_active_wheel = monitor.remember_active_wheel
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

    for record in list(state["pending_posts"].values()):
        if isinstance(record, dict):
            _pending_to_active(state, record)
    state["pending_posts"] = {}

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


def retry_unverified_wheels(state: dict, current=None) -> dict[str, int | bool]:
    """Retry only wheels admitted after a transient BetBoom API failure."""

    current = current or monitor.now_utc()
    active = state.setdefault("active_wheels", {})
    changed = False
    confirmed = 0
    removed = 0
    for key, entry in list(active.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("verification_status") != monitor.WHEEL_VERIFICATION_FAILED:
            continue
        retry_at = monitor.parse_datetime(entry.get("verification_retry_at"))
        if retry_at is not None and retry_at > current:
            continue
        url = str(entry.get("url") or "")
        if not url:
            continue
        inspection = monitor.inspect_wheel_page(url)
        monitor.record_wheel_api_verification(
            state, inspection, checked_at=current
        )
        entry["last_verification_at"] = current.isoformat()
        if inspection.status == "verification_failed":
            entry["verification_retry_at"] = (
                current + timedelta(minutes=monitor.PENDING_RECHECK_MINUTES)
            ).isoformat()
            changed = True
            continue
        if inspection.status == "inactive":
            active.pop(key, None)
            removed += 1
            changed = True
            continue

        entry["verification_status"] = monitor.WHEEL_VERIFICATION_CONFIRMED
        entry["status"] = "active"
        entry["page_status"] = "active"
        entry["method"] = inspection.method[:300]
        entry.pop("verification_retry_at", None)
        if inspection.action_id is not None:
            entry["action_id"] = inspection.action_id
            state.setdefault("wheel_action_history", {})[str(key).casefold()] = {
                "action_id": inspection.action_id,
                "seen_at": current.isoformat(),
            }
        if inspection.available_at is not None:
            entry["available_at"] = inspection.available_at.isoformat()
        if inspection.deadline is not None:
            entry["deadline"] = inspection.deadline.isoformat()
            entry["expires_at"] = monitor.participation_expiry(
                inspection.deadline, current=current
            ).isoformat()
            entry["needs_manual_time"] = False
        else:
            entry.pop("deadline", None)
            entry["expires_at"] = _entry_untimed_expiry(
                entry, current
            ).isoformat()
            entry["needs_manual_time"] = True
        confirmed += 1
        changed = True
    return {"changed": changed, "confirmed": confirmed, "removed": removed}


def process_active_without_page_verdict(state: dict, stats: dict):
    current = monitor.now_utc()
    verification = retry_unverified_wheels(state, current)
    changed = bool(verification.get("changed"))
    state.setdefault("pending_posts", {}).clear()

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

    state.setdefault("pending_posts", {}).clear()
    if changed:
        result["changed"] = True
    result["verification_confirmed"] = int(verification.get("confirmed", 0) or 0)
    result["verification_removed"] = int(verification.get("removed", 0) or 0)
    result["pending_total"] = 0
    return result


monitor.load_state = load_state_without_pending
monitor.remember_pending = remember_without_pending
monitor.assess_new_wheel = assess_new_without_pending
monitor.assess_pending_wheel = assess_pending_without_pending
monitor.wheel_reply_markup = wheel_reply_markup_bbvg
monitor.process_active_wheels = process_active_without_page_verdict


if __name__ == "__main__":
    raise SystemExit(monitor.main())
