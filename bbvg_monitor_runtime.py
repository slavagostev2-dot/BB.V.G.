from __future__ import annotations

from datetime import timedelta
from typing import Any

import monitor_entry as base_runtime


monitor = base_runtime.monitor
MANUAL_WHEEL_TTL_DAYS = 7
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


def _manual_expiry(current=None):
    current = current or monitor.now_utc()
    return current + timedelta(days=MANUAL_WHEEL_TTL_DAYS)


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
            existing["expires_at"] = _manual_expiry(current).isoformat()
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
        else _manual_expiry(current)
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
    if _inactive_entry(state, key):
        state.setdefault("seen", {})[post_key] = monitor.now_utc().isoformat()
        return

    canonical = base_runtime._CANONICAL_MESSAGES.get(key, message)
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
        entry["needs_manual_time"] = deadline is None
        entry["last_checked_at"] = monitor.now_utc().isoformat()
        if deadline is None:
            entry.pop("deadline", None)
            entry["expires_at"] = _manual_expiry().isoformat()
        else:
            entry["deadline"] = deadline.isoformat()
            entry["expires_at"] = monitor.participation_expiry(deadline).isoformat()

    state.setdefault("pending_posts", {}).clear()
    if status != "send_error":
        state.setdefault("seen", {})[post_key] = monitor.now_utc().isoformat()


def assess_new_without_pending(message, link, state=None):
    if isinstance(state, dict) and _inactive_entry(state, monitor.wheel_key(link)):
        return monitor.WheelAssessment(
            False,
            None,
            "колесо отмечено администратором как неактивное",
            "inactive",
            "",
        )
    return _original_assess_new(message, link, state)


def assess_pending_without_pending(message, link, state=None):
    if isinstance(state, dict) and _inactive_entry(state, monitor.wheel_key(link)):
        return monitor.WheelAssessment(
            False,
            None,
            "колесо отмечено администратором как неактивное",
            "inactive",
            "",
        )
    return _original_assess_pending(message, link, state)


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


def process_active_without_page_verdict(state: dict, stats: dict):
    current = monitor.now_utc()
    changed = False
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
            minimum_expiry = _manual_expiry(current)
            existing_expiry = monitor.parse_datetime(entry.get("expires_at"))
            if existing_expiry is None or existing_expiry < minimum_expiry:
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
