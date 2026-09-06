from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import bbvg_monitor_main as subject


UTC = timezone.utc


class WheelTimeRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
        self.sent: list[dict[str, Any]] = []
        self.original_process_active = subject._original_process_active
        self.original_now = subject.monitor.now_utc
        self.original_send_message = subject.monitor.send_message
        self.original_active_entry_message = subject.monitor.active_entry_message
        self.original_wheel_reply_markup = subject.monitor.wheel_reply_markup
        self.original_untimed_expiry = subject.runtime._entry_untimed_expiry

        subject.monitor.now_utc = lambda: self.current
        subject.runtime._entry_untimed_expiry = (
            lambda entry, current: current + timedelta(hours=2)
        )
        subject.monitor.active_entry_message = lambda entry: subject.monitor.Message(
            source=str(entry.get("source") or "source"),
            message_id=1,
            date=self.current - timedelta(minutes=5),
            text=str(entry.get("url") or ""),
            message_url="https://telegram.me/source/1",
        )
        subject.monitor.wheel_reply_markup = lambda *args, **kwargs: {
            "inline_keyboard": [[{"text": "Открыть колесо", "url": args[2]}]]
        }

        def send_message(text: str, url=None, reply_markup=None):
            self.sent.append(
                {
                    "text": text,
                    "url": url,
                    "reply_markup": reply_markup,
                }
            )
            return {"ok": True, "result": {"message_id": len(self.sent)}}

        subject.monitor.send_message = send_message

    def tearDown(self) -> None:
        subject._original_process_active = self.original_process_active
        subject.monitor.now_utc = self.original_now
        subject.monitor.send_message = self.original_send_message
        subject.monitor.active_entry_message = self.original_active_entry_message
        subject.monitor.wheel_reply_markup = self.original_wheel_reply_markup
        subject.runtime._entry_untimed_expiry = self.original_untimed_expiry

    def _state(self) -> dict[str, Any]:
        return {
            "active_wheels": {
                "wheel-a": {
                    "identifier": "wheel-a",
                    "url": "https://betboom.ru/freestream/wheel-a",
                    "source": "source",
                    "message_id": 1,
                    "message_date": (
                        self.current - timedelta(minutes=5)
                    ).isoformat(),
                    "message_url": "https://telegram.me/source/1",
                    "message_text": "https://betboom.ru/freestream/wheel-a",
                    "needs_manual_time": True,
                    "manual_time_waiting_since": (
                        self.current - timedelta(minutes=10)
                    ).isoformat(),
                    "expires_at": (self.current + timedelta(hours=2)).isoformat(),
                }
            }
        }

    def test_exact_time_transition_notifies_once_and_removes_manual_wait(self) -> None:
        deadline = self.current + timedelta(hours=1)

        def refresh(state: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
            state["active_wheels"]["wheel-a"]["deadline"] = deadline.isoformat()
            state["active_wheels"]["wheel-a"]["method"] = "BetBoom API"
            return {
                "tracked": 1,
                "known_reminders": 0,
                "unknown_reminders": 0,
                "removed": 0,
                "changed": True,
            }

        subject._original_process_active = refresh
        state = self._state()
        stats: dict[str, Any] = {"sources": {}, "daily": {}}

        result = subject.process_active_without_unknown_time_spam(state, stats)
        entry = state["active_wheels"]["wheel-a"]

        self.assertNotIn("needs_manual_time", entry)
        self.assertNotIn("manual_time_waiting_since", entry)
        self.assertEqual(result["time_known_notifications"], 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Время прокрутки колеса определено", self.sent[0]["text"])
        self.assertIn("Колесо BetBoom стало активно", self.sent[0]["text"])
        self.assertTrue(entry.get("time_became_known_at"))
        self.assertTrue(entry.get("time_known_notified_at"))
        self.assertEqual(
            stats["sources"]["source"]["time_known_notifications"],
            1,
        )

        second = subject.process_active_without_unknown_time_spam(state, stats)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("time_known_notifications", second)

    def test_untimed_wheel_stays_under_automatic_recheck_without_notification(self) -> None:
        subject._original_process_active = lambda state, stats: {
            "tracked": 1,
            "known_reminders": 0,
            "unknown_reminders": 0,
            "removed": 0,
            "changed": False,
        }
        state = self._state()

        result = subject.process_active_without_unknown_time_spam(state, {})
        entry = state["active_wheels"]["wheel-a"]

        self.assertNotIn("needs_manual_time", entry)
        self.assertNotIn("manual_time_waiting_since", entry)
        self.assertTrue(entry.get("last_unknown_reminder_at"))
        self.assertEqual(self.sent, [])
        self.assertTrue(result["changed"])


if __name__ == "__main__":
    unittest.main()
