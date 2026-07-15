from __future__ import annotations

import inspect
from pathlib import Path

import admin_action_v3
import wheel_lifecycle_v2


ROOT = Path(__file__).resolve().parent


def main() -> int:
    transitions = set(wheel_lifecycle_v2.LIFECYCLE_TRANSITIONS)
    required = {
        ("detected", "future_availability", "scheduled_availability"),
        ("detected", "known_draw_time", "scheduled_draw"),
        ("detected", "unknown_draw_time", "active_unknown_time"),
        ("scheduled_availability", "availability_reached", "active_unknown_time"),
        ("active_unknown_time", "manual_time_set", "scheduled_draw"),
        ("scheduled_draw", "deadline_reached", "finished"),
        ("participating", "deadline_reached", "finished"),
        ("participating", "admin_inactive", "inactive"),
    }
    assert required <= transitions
    assert wheel_lifecycle_v2.FINAL_REMINDER_BEFORE_MINUTES == 5
    assert "rating_event_key" in inspect.getsource(admin_action_v3._original_apply_action)
    finished_source = inspect.getsource(admin_action_v3.confirm_finished_global)
    assert "record_admin_wheel_decision" in finished_source
    assert 'decision="confirmed"' in finished_source
    assert (ROOT / "tests/test_chapter5_lifecycle.py").exists()
    assert "Mini App, Worker и D1 остаются архивированными" in (
        ROOT / "CHAPTER_5_RU.md"
    ).read_text(encoding="utf-8")
    print("Chapter 5 full wheel lifecycle acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
