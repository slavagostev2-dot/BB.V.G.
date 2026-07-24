from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any, Callable

import notification_router


_POST_TIME_RE = re.compile(r"Пост:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})")
_context = threading.local()


class WheelNotificationNotDelivered(RuntimeError):
    """A wheel was detected, but Telegram confirmed no recipient delivery."""


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _result(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    value = response.get("result")
    return value if isinstance(value, dict) else {}


def _structured_delivery_response(response: Any) -> bool:
    result = _result(response)
    return bool(
        result
        and {
            "sent",
            "suppressed",
            "hidden_skipped",
            "category",
            "kind",
            "message_id",
        }
        & set(result)
    )


def _delivered(response: Any) -> bool:
    result = _result(response)
    return _positive_int(result.get("sent")) > 0 or _positive_int(
        result.get("message_id")
    ) > 0


def _parse_event_anchor(monitor_module: Any, text: str, wheel_key: str) -> datetime | None:
    match = _POST_TIME_RE.search(str(text or ""))
    if match:
        try:
            value = datetime.strptime(match.group(1), "%d.%m.%Y %H:%M")
            return value.replace(tzinfo=monitor_module.DISPLAY_TZ).astimezone(
                monitor_module.UTC
            )
        except (TypeError, ValueError, AttributeError):
            pass

    try:
        state = monitor_module.load_state()
    except Exception:
        return None
    active = state.get("active_wheels") if isinstance(state, dict) else None
    entry = active.get(wheel_key) if isinstance(active, dict) else None
    if not isinstance(entry, dict):
        return None
    for field in ("server_start_at", "message_date", "first_notified_at"):
        try:
            parsed = monitor_module.parse_datetime(entry.get(field))
        except Exception:
            parsed = None
        if parsed is not None:
            return parsed.astimezone(monitor_module.UTC)
    return None


def _hidden_record(config: dict[str, Any], chat_id: str, wheel_key: str) -> dict[str, Any]:
    _user_id, record = notification_router.user_for_chat(config, chat_id)
    raw = record.get("hidden_wheels") if isinstance(record, dict) else None
    if not isinstance(raw, dict):
        return {}
    value = raw.get(wheel_key)
    return value if isinstance(value, dict) else {}


def _generation_aware_hidden(
    monitor_module: Any,
    original_hidden: Callable[[dict[str, Any], str, str], bool],
    config: dict[str, Any],
    chat_id: str,
    wheel_key: str,
) -> bool:
    if not original_hidden(config, chat_id, wheel_key):
        return False

    event_anchor = getattr(_context, "event_anchor", None)
    if event_anchor is None:
        return True
    record = _hidden_record(config, chat_id, wheel_key)
    hidden_at = None
    try:
        hidden_at = monitor_module.parse_datetime(record.get("hidden_at"))
    except Exception:
        hidden_at = None
    if hidden_at is None:
        return True

    # A personal hide belongs to the wheel generation that existed when the user
    # pressed the button. Reusing the same BetBoom identifier on a later day must
    # not hide a new action for another 30 days.
    return event_anchor <= hidden_at.astimezone(monitor_module.UTC)


def _eligible_targets(
    config: dict[str, Any],
    exists: bool,
    kind: str,
    wheel_key: str,
) -> tuple[list[str], list[str]]:
    targets = notification_router.recipients(config, exists, kind)
    visible = [
        chat_id
        for chat_id in targets
        if not notification_router.hidden_for_chat(config, chat_id, wheel_key)
    ]
    return targets, visible


def _completed_for_all(
    visible_targets: list[str],
    kind: str,
    event_identity: str,
    url: str | None,
) -> bool:
    status_reader = getattr(notification_router, "delivery_reservation_status", None)
    if not callable(status_reader) or not visible_targets or not event_identity:
        return False
    statuses = []
    for chat_id in visible_targets:
        key = notification_router.delivery_key(
            chat_id,
            kind,
            event_identity,
            None if event_identity else url,
        )
        statuses.append(str(status_reader(key) or "unknown"))
    return bool(statuses and all(status == "completed" for status in statuses))


def _validate_wheel_delivery(
    response: Any,
    *,
    text: str,
    url: str | None,
    reply_markup: dict | None,
) -> None:
    if not _structured_delivery_response(response) or _delivered(response):
        return

    result = _result(response)
    reason = str(result.get("reason") or "")
    if reason == "referral_wheel_notifications_disabled":
        return

    kind = notification_router.notification_kind(text)
    wheel_key = notification_router.wheel_key_from_message(text, url, reply_markup)
    if not wheel_key or not (kind == "wheels" or kind.startswith("wheel_")):
        return

    config, exists = notification_router.load_config()
    targets, visible_targets = _eligible_targets(config, exists, kind, wheel_key)

    # A deliberate personal hide is a valid silence. Old hides from a previous
    # generation have already been rejected by the generation-aware wrapper.
    if targets and not visible_targets:
        return

    event_identity = notification_router.notification_event_identity(
        kind, text, url, reply_markup
    )
    if _completed_for_all(visible_targets, kind, event_identity, url):
        return

    if not targets:
        detail = "нет получателей с включёнными уведомлениями"
    elif not visible_targets:
        detail = "колесо скрыто всеми получателями"
    else:
        status_reader = getattr(notification_router, "delivery_reservation_status", None)
        statuses: list[str] = []
        if callable(status_reader) and event_identity:
            for chat_id in visible_targets:
                delivery_key = notification_router.delivery_key(
                    chat_id, kind, event_identity, None
                )
                statuses.append(str(status_reader(delivery_key) or "unknown"))
        detail = "Telegram подтвердил sent=0"
        if statuses:
            detail += "; состояния доставки: " + ",".join(sorted(set(statuses)))
    if reason:
        detail += f"; причина: {reason}"
    raise WheelNotificationNotDelivered(
        f"уведомление {wheel_key} не доставлено: {detail}"
    )


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_notification_delivery_guard_installed", False):
        return

    original_send = monitor_module.send_message
    original_hidden = notification_router.hidden_for_chat

    def hidden_for_current_generation(
        config: dict[str, Any], chat_id: str, wheel_key: str
    ) -> bool:
        return _generation_aware_hidden(
            monitor_module,
            original_hidden,
            config,
            chat_id,
            wheel_key,
        )

    def send_with_receipt(
        text: str,
        url: str | None = None,
        reply_markup: dict | None = None,
    ) -> Any:
        kind = notification_router.notification_kind(text)
        wheel_key = notification_router.wheel_key_from_message(
            text, url, reply_markup
        )
        guarded = bool(
            wheel_key and (kind == "wheels" or kind.startswith("wheel_"))
        )
        previous_anchor = getattr(_context, "event_anchor", None)
        if guarded:
            _context.event_anchor = _parse_event_anchor(
                monitor_module, text, wheel_key
            )
        try:
            response = original_send(text, url=url, reply_markup=reply_markup)
            if guarded:
                _validate_wheel_delivery(
                    response,
                    text=text,
                    url=url,
                    reply_markup=reply_markup,
                )
            return response
        finally:
            if previous_anchor is None:
                if hasattr(_context, "event_anchor"):
                    delattr(_context, "event_anchor")
            else:
                _context.event_anchor = previous_anchor

    notification_router.hidden_for_chat = hidden_for_current_generation
    monitor_module.send_message = send_with_receipt
    monitor_module._bbvg_notification_delivery_guard_installed = True


def self_test() -> None:
    assert _delivered({"result": {"sent": 1}})
    assert _delivered({"result": {"message_id": 10}})
    assert not _delivered({"result": {"sent": 0}})
    assert _structured_delivery_response(
        {"result": {"suppressed": True, "kind": "wheels"}}
    )
    print("notification delivery guard self-test passed")


if __name__ == "__main__":
    self_test()
