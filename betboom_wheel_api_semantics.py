from __future__ import annotations

from datetime import timedelta
from typing import Any


RECENT_CLOSED_PROBE_AGE = timedelta(hours=2)
RECENT_CLOSED_PROBE_LIMIT = 3


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

    print("BetBoom wheel API semantics self-test passed")


if __name__ == "__main__":
    self_test()
