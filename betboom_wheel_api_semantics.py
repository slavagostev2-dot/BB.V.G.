from __future__ import annotations

import html
from datetime import timedelta
from typing import Any


RECENT_CLOSED_PROBE_AGE = timedelta(hours=2)
RECENT_CLOSED_PROBE_LIMIT = 3
TIME_KNOWN_NOTIFICATION_TYPE = "wheel_time_known"
TIME_KNOWN_MARKUP_EVENT_KEY = "_bbvg_event_id"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def classify_info(monitor_module: Any, info: dict[str, Any], *, current=None):
    """Classify BetBoom action-info without treating ``is_early`` as ended."""

    current = current or monitor_module.now_utc()
    action_id = _positive_int(info.get("action_id"))
    start = monitor_module.parse_datetime(info.get("start_dttm"))
    duration = _positive_float(
        info.get("duration_min", info.get("duration_in_minutes"))
    )
    deadline = start + timedelta(minutes=duration) if start and duration else None
    is_ended = bool(info.get("is_ended"))
    is_early = bool(info.get("is_early"))

    if is_ended or (deadline is not None and deadline <= current):
        return monitor_module.WheelInspection(
            "inactive",
            deadline,
            "BetBoom подтвердил, что время участия истекло",
            action_id=action_id,
            server_start_at=start,
            verification_status=monitor_module.WHEEL_VERIFICATION_CONFIRMED,
        )

    if is_early and (start is None or start > current):
        return monitor_module.WheelInspection(
            "not_started",
            None,
            "BetBoom создал колесо, но участие ещё не открыто",
            action_id=action_id,
            available_at=start if start and start > current else None,
            server_start_at=start,
            verification_status=monitor_module.WHEEL_VERIFICATION_CONFIRMED,
        )

    if action_id is not None and duration is not None and start is None:
        return monitor_module.WheelInspection(
            "not_started",
            None,
            "BetBoom создал колесо, но участие ещё не открыто",
            action_id=action_id,
            server_start_at=start,
            verification_status=monitor_module.WHEEL_VERIFICATION_CONFIRMED,
        )

    available_at = start if start and start > current else None
    return monitor_module.WheelInspection(
        "active",
        deadline,
        "активность и таймер подтверждены BetBoom"
        if deadline
        else "активность подтверждена BetBoom, таймер не указан",
        action_id=action_id,
        available_at=available_at,
        server_start_at=start,
        verification_status=monitor_module.WHEEL_VERIFICATION_CONFIRMED,
    )


def _recent_closed_keys(monitor_module: Any, state: dict[str, Any]) -> list[str]:
    current = monitor_module.now_utc()
    history = state.get("wheel_action_history")
    if not isinstance(history, dict):
        return []
    rows: list[tuple[Any, str]] = []
    for key, raw in history.items():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("state") or "").casefold() not in {"closed", "inactive", "finished"}:
            continue
        seen = monitor_module.parse_datetime(raw.get("closed_at") or raw.get("seen_at"))
        if seen is None or seen > current or current - seen > RECENT_CLOSED_PROBE_AGE:
            continue
        rows.append((seen, str(key).casefold()))
    rows.sort(reverse=True)
    return [key for _, key in rows[:RECENT_CLOSED_PROBE_LIMIT]]


def _event_id(monitor_module: Any, key: str, entry: dict[str, Any]) -> str:
    explicit = str(entry.get("canonical_event_id") or "").strip().casefold()
    if explicit:
        return explicit
    try:
        return str(
            monitor_module.event_id_from_entry(entry, wheel_key=key) or ""
        ).strip().casefold()
    except Exception:
        return ""


def _time_known_delivery_already_sent(
    monitor_module: Any,
    event_id: str,
    entry: dict[str, Any],
) -> bool:
    if not event_id:
        return False
    if (
        str(entry.get("time_known_notification_event_id") or "").casefold()
        == event_id
        and monitor_module.parse_datetime(entry.get("time_known_notified_at"))
        is not None
    ):
        return True
    try:
        snapshot = monitor_module.event_store().event_snapshot(event_id)
    except Exception:
        return False
    for raw in snapshot.get("notifications", []):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("notification_type") or "") != TIME_KNOWN_NOTIFICATION_TYPE:
            continue
        if raw.get("sent_at") or raw.get("telegram_message_id"):
            entry["time_known_notification_event_id"] = event_id
            entry["time_known_notified_at"] = str(
                raw.get("sent_at") or monitor_module.now_utc().isoformat()
            )
            return True
    return False


def _record_time_known_deliveries(
    monitor_module: Any,
    event_id: str,
    response: Any,
) -> int:
    if not event_id:
        return 0
    result = response.get("result") if isinstance(response, dict) else {}
    result = result if isinstance(result, dict) else {}
    deliveries = result.get("deliveries")
    deliveries = deliveries if isinstance(deliveries, list) else []
    recorded = 0
    for raw in deliveries:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "unknown")
        try:
            monitor_module.event_store().record_notification(
                event_id,
                notification_type=TIME_KNOWN_NOTIFICATION_TYPE,
                recipient_scope=str(
                    raw.get("recipient_scope") or "configured_recipients"
                ),
                status=status,
                telegram_message_id=raw.get("telegram_message_id"),
                error_text=str(raw.get("error_type") or ""),
                sent_at=(
                    monitor_module.now_utc() if status == "sent" else None
                ),
            )
        except Exception as exc:
            print(
                "WARNING wheel time-known delivery audit: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            recorded += 1
    return recorded


def _notify_time_known(
    monitor_module: Any,
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any],
    deadline: Any,
) -> bool:
    event_id = _event_id(monitor_module, key, entry)
    if _time_known_delivery_already_sent(monitor_module, event_id, entry):
        return True

    identifier = html.escape(str(entry.get("identifier") or key))
    local_deadline = deadline.astimezone(monitor_module.DISPLAY_TZ)
    url = str(entry.get("url") or "")
    markup: dict[str, Any] = {
        "inline_keyboard": [
            [{"text": "🎡 Открыть колесо", "url": url}]
        ] if url else []
    }
    if event_id:
        markup[TIME_KNOWN_MARKUP_EVENT_KEY] = event_id
    response = monitor_module.send_message(
        "🕐 <b>Время колеса определено</b>\n\n"
        f"Идентификатор: <code>{identifier}</code>\n"
        f"Время прокрутки: <b>{local_deadline:%d.%m %H:%M}</b>",
        reply_markup=markup,
    )
    _record_time_known_deliveries(monitor_module, event_id, response)
    result = response.get("result") if isinstance(response, dict) else {}
    result = result if isinstance(result, dict) else {}
    try:
        failed = int(result.get("failed", 0) or 0)
    except (TypeError, ValueError):
        failed = 0
    if failed:
        entry["time_known_notification_error"] = f"failed_recipients={failed}"
        return False

    entry["time_known_notification_event_id"] = event_id
    entry["time_known_notified_at"] = monitor_module.now_utc().isoformat()
    entry["time_known_deadline"] = deadline.isoformat()
    entry.pop("time_known_notification_error", None)
    return True


def _mark_time_known_transition(
    monitor_module: Any,
    key: str,
    entry: dict[str, Any],
    *,
    detected_at: Any,
) -> None:
    event_id = _event_id(monitor_module, key, entry)
    if (
        str(entry.get("time_known_notification_event_id") or "").casefold()
        == event_id
        and entry.get("time_known_detected_at")
    ):
        return
    deadline = monitor_module.parse_datetime(entry.get("deadline"))
    if deadline is None:
        return
    entry["time_known_notification_event_id"] = event_id
    entry["time_known_detected_at"] = detected_at.isoformat()
    entry["time_known_deadline"] = deadline.isoformat()
    if event_id:
        try:
            monitor_module.event_store().record_transition(
                event_id,
                "wheel_time_known",
                occurred_at=detected_at,
                payload={"deadline": deadline.isoformat(), "wheel_key": key},
                dedupe_key=deadline.isoformat(),
            )
        except Exception as exc:
            print(
                "WARNING wheel time-known transition audit: "
                f"{type(exc).__name__}: {exc}"
            )


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_betboom_wheel_api_semantics_installed", False):
        return

    def inspect_wheel_page_authoritative(link: str):
        normalized = monitor_module.normalize_url(link)
        try:
            action_uid, signature = monitor_module.wheel_action_credentials(normalized)
            response = monitor_module.request_with_retries(
                "POST",
                monitor_module.BETBOOM_WHEEL_INFO_URL,
                attempts=monitor_module.WHEEL_API_ATTEMPTS,
                timeout=monitor_module.REQUEST_TIMEOUT,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": monitor_module.USER_AGENT,
                    "x-platform": "web",
                    "x-action-signature": signature,
                },
                json={"action_uid": action_uid},
            )
        except (monitor_module.requests.RequestException, ValueError) as exc:
            return monitor_module._wheel_verification_failed(
                f"{type(exc).__name__}: {exc}"
            )

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            return monitor_module._wheel_verification_failed(
                f"invalid JSON, HTTP {response.status_code}: {type(exc).__name__}"
            )
        if not isinstance(payload, dict):
            return monitor_module._wheel_verification_failed(
                f"unexpected JSON type {type(payload).__name__}, HTTP {response.status_code}"
            )

        error_message = monitor_module._api_error_message(payload)
        info = payload.get("info")
        api_code = payload.get("code")
        if not isinstance(info, dict):
            if "не найд" in error_message.casefold():
                result = monitor_module.WheelInspection(
                    "inactive",
                    None,
                    "BetBoom не нашёл действующее колесо по этой ссылке",
                    verification_status=monitor_module.WHEEL_VERIFICATION_CONFIRMED,
                )
                print(
                    f"BetBoom action-info: wheel={monitor_module.wheel_key(normalized)} "
                    "status=inactive reason=not_found"
                )
                return result
            return monitor_module._wheel_verification_failed(
                f"API code={api_code!r}, HTTP {response.status_code}, error={error_message!r}"
            )

        try:
            response.raise_for_status()
        except monitor_module.requests.RequestException as exc:
            return monitor_module._wheel_verification_failed(
                f"{type(exc).__name__}: {exc}"
            )

        current = monitor_module.now_utc()
        result = classify_info(monitor_module, info, current=current)
        action_id = _positive_int(info.get("action_id"))
        start = monitor_module.parse_datetime(info.get("start_dttm"))
        duration = _positive_float(
            info.get("duration_min", info.get("duration_in_minutes"))
        )
        deadline = start + timedelta(minutes=duration) if start and duration else None
        print(
            "BetBoom action-info: "
            f"wheel={monitor_module.wheel_key(normalized)} "
            f"action_id={action_id or 0} "
            f"start={start.isoformat() if start else '-'} "
            f"duration_min={duration if duration is not None else '-'} "
            f"is_early={bool(info.get('is_early'))} "
            f"is_ended={bool(info.get('is_ended'))} "
            f"deadline={deadline.isoformat() if deadline else '-'} "
            f"status={result.status}"
        )
        return result

    monitor_module.inspect_wheel_page = inspect_wheel_page_authoritative

    original_process_active_wheels = getattr(
        monitor_module, "process_active_wheels", None
    )
    if callable(original_process_active_wheels):
        def process_active_wheels_with_time_known(
            state: dict[str, Any], stats: dict[str, Any]
        ):
            active = state.get("active_wheels")
            active = active if isinstance(active, dict) else {}
            untimed_before = {
                str(key).casefold()
                for key, raw in active.items()
                if isinstance(raw, dict)
                and monitor_module.parse_datetime(raw.get("deadline")) is None
            }
            result = original_process_active_wheels(state, stats)
            current = monitor_module.now_utc()
            active_after = state.get("active_wheels")
            active_after = active_after if isinstance(active_after, dict) else {}

            for raw_key, raw in active_after.items():
                if not isinstance(raw, dict):
                    continue
                key = str(raw_key).casefold()
                deadline = monitor_module.parse_datetime(raw.get("deadline"))
                if deadline is None:
                    continue
                if key in untimed_before:
                    _mark_time_known_transition(
                        monitor_module,
                        key,
                        raw,
                        detected_at=current,
                    )
                event_id = _event_id(monitor_module, key, raw)
                if (
                    raw.get("time_known_detected_at")
                    and str(
                        raw.get("time_known_notification_event_id") or ""
                    ).casefold()
                    == event_id
                    and not _time_known_delivery_already_sent(
                        monitor_module, event_id, raw
                    )
                ):
                    try:
                        completed = _notify_time_known(
                            monitor_module, state, key, raw, deadline
                        )
                    except Exception as exc:
                        raw["time_known_notification_error"] = (
                            f"{type(exc).__name__}: {exc}"
                        )[:300]
                        print(
                            "WARNING wheel time-known notification: "
                            f"wheel={key} {type(exc).__name__}: {exc}"
                        )
                        completed = False
                    if completed and isinstance(result, dict):
                        result["changed"] = True
            return result

        monitor_module.process_active_wheels = process_active_wheels_with_time_known

    original_main = getattr(monitor_module, "main", None)
    if callable(original_main):
        def main_with_recent_closed_probe(*args: Any, **kwargs: Any):
            try:
                state = monitor_module.load_state()
                for key in _recent_closed_keys(monitor_module, state):
                    result = inspect_wheel_page_authoritative(
                        f"https://betboom.ru/freestream/{key}"
                    )
                    print(
                        "BetBoom recent-closed probe: "
                        f"wheel={key} status={result.status} "
                        f"deadline={result.deadline.isoformat() if result.deadline else '-'}"
                    )
            except Exception as exc:
                print(f"WARNING BetBoom recent-closed probe: {type(exc).__name__}: {exc}")
            return original_main(*args, **kwargs)

        monitor_module.main = main_with_recent_closed_probe

    monitor_module._bbvg_betboom_wheel_api_semantics_installed = True


def self_test() -> None:
    from datetime import datetime, timezone
    from types import SimpleNamespace

    utc = timezone.utc
    current = datetime(2026, 8, 26, 16, 0, tzinfo=utc)

    class Inspection:
        def __init__(self, status, deadline, method, **kwargs):
            self.status = status
            self.deadline = deadline
            self.method = method
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake = SimpleNamespace(
        now_utc=lambda: current,
        parse_datetime=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None,
        WheelInspection=Inspection,
        WHEEL_VERIFICATION_CONFIRMED="confirmed",
    )

    active_early = classify_info(
        fake,
        {
            "action_id": 0,
            "start_dttm": "2026-08-26T15:57:46+00:00",
            "is_early": True,
            "is_ended": False,
        },
        current=current,
    )
    assert active_early.status == "active"
    assert active_early.deadline is None

    future_early = classify_info(
        fake,
        {
            "action_id": 701,
            "start_dttm": "2026-08-26T16:30:00+00:00",
            "duration_min": 60,
            "is_early": True,
            "is_ended": False,
        },
        current=current,
    )
    assert future_early.status == "not_started"

    ended = classify_info(
        fake,
        {
            "action_id": 701,
            "start_dttm": "2026-08-26T15:00:00+00:00",
            "duration_min": 30,
            "is_ended": True,
        },
        current=current,
    )
    assert ended.status == "inactive"

    timed_active = classify_info(
        fake,
        {
            "action_id": 702,
            "start_dttm": "2026-08-26T15:45:00+00:00",
            "duration_min": 60,
            "is_ended": False,
        },
        current=current,
    )
    assert timed_active.status == "active"
    assert timed_active.deadline == datetime(2026, 8, 26, 16, 45, tzinfo=utc)

    state = {
        "wheel_action_history": {
            "zonertg13": {
                "state": "closed",
                "seen_at": "2026-08-26T15:59:00+00:00",
            },
            "old": {
                "state": "closed",
                "seen_at": "2026-08-26T12:00:00+00:00",
            },
        }
    }
    assert _recent_closed_keys(fake, state) == ["zonertg13"]

    class Store:
        def __init__(self):
            self.notifications: list[dict[str, Any]] = []
            self.transitions: list[dict[str, Any]] = []

        def event_snapshot(self, event_id: str) -> dict[str, Any]:
            return {"notifications": list(self.notifications)}

        def record_notification(self, event_id: str, **kwargs: Any) -> str:
            self.notifications.append(dict(kwargs))
            return "delivery"

        def record_transition(self, event_id: str, stage: str, **kwargs: Any) -> bool:
            self.transitions.append({"event_id": event_id, "stage": stage, **kwargs})
            return True

    store = Store()
    sent: list[tuple[str, dict[str, Any]]] = []
    target_deadline = datetime(2026, 8, 26, 17, 30, tzinfo=utc)
    active_state = {
        "active_wheels": {
            "wheel-a": {
                "identifier": "wheel-a",
                "url": "https://betboom.ru/freestream/wheel-a",
                "canonical_event_id": "evt:11111111111111111111",
            }
        }
    }

    def original_process(state_value: dict[str, Any], stats: dict[str, Any]):
        state_value["active_wheels"]["wheel-a"]["deadline"] = (
            target_deadline.isoformat()
        )
        return {"tracked": 1, "changed": True}

    runtime = SimpleNamespace(
        now_utc=lambda: current,
        parse_datetime=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None,
        event_id_from_entry=lambda entry, wheel_key="": entry.get("canonical_event_id", ""),
        event_store=lambda: store,
        DISPLAY_TZ=utc,
        send_message=lambda text, **kwargs: sent.append((text, kwargs)) or {
            "ok": True,
            "result": {
                "failed": 0,
                "deliveries": [
                    {
                        "recipient_scope": "recipient:test",
                        "status": "sent",
                        "telegram_message_id": 77,
                    }
                ],
            },
        },
        process_active_wheels=original_process,
        normalize_url=lambda value: value,
        wheel_action_credentials=lambda value: ("uid", "sig"),
        request_with_retries=lambda *args, **kwargs: None,
        BETBOOM_WHEEL_INFO_URL="https://example.invalid",
        WHEEL_API_ATTEMPTS=1,
        REQUEST_TIMEOUT=1,
        USER_AGENT="test",
        requests=SimpleNamespace(RequestException=Exception),
        _wheel_verification_failed=lambda detail: None,
        _api_error_message=lambda payload: "",
        wheel_key=lambda value: "wheel-a",
        load_state=lambda: active_state,
        WheelInspection=Inspection,
        WHEEL_VERIFICATION_CONFIRMED="confirmed",
    )
    install(runtime)
    runtime.process_active_wheels(active_state, {})
    assert len(sent) == 1
    assert "Время колеса определено" in sent[0][0]
    assert "26.08 17:30" in sent[0][0]
    assert active_state["active_wheels"]["wheel-a"]["time_known_notified_at"]
    assert store.transitions[0]["stage"] == "wheel_time_known"
    assert store.notifications[0]["notification_type"] == TIME_KNOWN_NOTIFICATION_TYPE
    runtime.process_active_wheels(active_state, {})
    assert len(sent) == 1

    print("BetBoom wheel API semantics self-test passed")


if __name__ == "__main__":
    self_test()
