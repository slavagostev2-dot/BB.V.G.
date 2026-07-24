from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import bbvg_monitor_main


UTC = timezone.utc


class ActiveButtonRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_now = bbvg_monitor_main.monitor.now_utc

    def tearDown(self) -> None:
        bbvg_monitor_main.monitor.now_utc = self.original_now

    def test_current_button_miss_retries_twice_while_timer_is_active(self) -> None:
        current = datetime(2026, 7, 24, 14, 5, tzinfo=UTC)
        bbvg_monitor_main.monitor.now_utc = lambda: current
        entry = {
            "deadline": (current + timedelta(minutes=12)).isoformat(),
            "verification_status": (
                bbvg_monitor_main.monitor.WHEEL_VERIFICATION_CONFIRMED
            ),
            "url": "https://betboom.ru/freestream/deko2",
        }
        record = {
            "status": "button_not_found",
            "attempt_version": 2,
            "attempted_at": (current - timedelta(minutes=3)).isoformat(),
        }

        self.assertEqual(
            bbvg_monitor_main.recoverable_active_button_not_found(record, entry),
            "active_button_not_found_retry",
        )
        self.assertEqual(entry["auto_participation_button_retry_count"], 1)

        current = current + timedelta(minutes=3)
        bbvg_monitor_main.monitor.now_utc = lambda: current
        record["attempted_at"] = (current - timedelta(minutes=3)).isoformat()
        self.assertEqual(
            bbvg_monitor_main.recoverable_active_button_not_found(record, entry),
            "active_button_not_found_retry",
        )
        self.assertEqual(entry["auto_participation_button_retry_count"], 2)

        current = current + timedelta(minutes=3)
        bbvg_monitor_main.monitor.now_utc = lambda: current
        record["attempted_at"] = (current - timedelta(minutes=3)).isoformat()
        self.assertEqual(
            bbvg_monitor_main.recoverable_active_button_not_found(record, entry),
            "",
        )

    def test_button_miss_waits_for_retry_interval(self) -> None:
        current = datetime(2026, 7, 24, 14, 5, tzinfo=UTC)
        bbvg_monitor_main.monitor.now_utc = lambda: current
        entry = {
            "deadline": (current + timedelta(minutes=12)).isoformat(),
            "verification_status": (
                bbvg_monitor_main.monitor.WHEEL_VERIFICATION_CONFIRMED
            ),
            "url": "https://betboom.ru/freestream/deko2",
        }
        record = {
            "status": "button_not_found",
            "attempt_version": 2,
            "attempted_at": (current - timedelta(minutes=1)).isoformat(),
        }
        self.assertEqual(
            bbvg_monitor_main.recoverable_active_button_not_found(record, entry),
            "",
        )
        self.assertNotIn("auto_participation_button_retry_count", entry)

    def test_button_miss_does_not_retry_near_deadline(self) -> None:
        current = datetime(2026, 7, 24, 14, 15, tzinfo=UTC)
        bbvg_monitor_main.monitor.now_utc = lambda: current
        entry = {
            "deadline": (current + timedelta(minutes=1)).isoformat(),
            "verification_status": (
                bbvg_monitor_main.monitor.WHEEL_VERIFICATION_CONFIRMED
            ),
            "url": "https://betboom.ru/freestream/deko2",
        }
        record = {
            "status": "button_not_found",
            "attempt_version": 2,
            "attempted_at": (current - timedelta(minutes=3)).isoformat(),
        }
        self.assertEqual(
            bbvg_monitor_main.recoverable_active_button_not_found(record, entry),
            "",
        )
        self.assertNotIn("auto_participation_button_retry_count", entry)

    def test_existing_legacy_upgrade_rule_is_preserved(self) -> None:
        current = datetime(2026, 7, 24, 14, 5, tzinfo=UTC)
        bbvg_monitor_main.monitor.now_utc = lambda: current
        entry = {
            "deadline": (current + timedelta(minutes=12)).isoformat(),
            "verification_status": (
                bbvg_monitor_main.monitor.WHEEL_VERIFICATION_CONFIRMED
            ),
            "url": "https://betboom.ru/freestream/deko2",
        }
        record = {
            "status": "button_not_found",
            "attempt_version": 1,
            "attempted_at": (current - timedelta(minutes=3)).isoformat(),
        }
        self.assertEqual(
            bbvg_monitor_main.recoverable_active_button_not_found(record, entry),
            "browser_attempt_upgrade",
        )
        self.assertNotIn("auto_participation_button_retry_count", entry)


if __name__ == "__main__":
    unittest.main()
