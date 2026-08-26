from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import betboom_wheel_api_semantics as semantics


class Inspection:
    def __init__(self, status, deadline, method, **kwargs):
        self.status = status
        self.deadline = deadline
        self.method = method
        for key, value in kwargs.items():
            setattr(self, key, value)


def fake_monitor(current: datetime):
    return SimpleNamespace(
        now_utc=lambda: current,
        parse_datetime=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None,
        WheelInspection=Inspection,
        WHEEL_VERIFICATION_CONFIRMED="confirmed",
    )


def test_current_start_with_is_early_is_not_archived():
    current = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)
    result = semantics.classify_info(
        fake_monitor(current),
        {
            "action_id": 0,
            "start_dttm": "2026-08-26T15:57:46.459000+00:00",
            "is_early": True,
            "is_ended": False,
        },
        current=current,
    )
    assert result.status == "active"
    assert result.deadline is None


def test_future_early_action_waits_instead_of_becoming_inactive():
    current = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)
    result = semantics.classify_info(
        fake_monitor(current),
        {
            "action_id": 801,
            "start_dttm": "2026-08-26T16:30:00+00:00",
            "duration_min": 60,
            "is_early": True,
            "is_ended": False,
        },
        current=current,
    )
    assert result.status == "not_started"


def test_explicit_ended_is_still_terminal():
    current = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)
    result = semantics.classify_info(
        fake_monitor(current),
        {
            "action_id": 802,
            "start_dttm": "2026-08-26T15:00:00+00:00",
            "duration_min": 120,
            "is_ended": True,
        },
        current=current,
    )
    assert result.status == "inactive"


def test_elapsed_server_deadline_is_terminal_even_without_end_flag():
    current = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)
    result = semantics.classify_info(
        fake_monitor(current),
        {
            "action_id": 803,
            "start_dttm": "2026-08-26T15:00:00+00:00",
            "duration_min": 30,
            "is_ended": False,
        },
        current=current,
    )
    assert result.status == "inactive"
