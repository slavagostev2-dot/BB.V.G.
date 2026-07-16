from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import bbvg_monitor_main
import monitor
import notification_router
import wheel_event_runtime


UTC = timezone.utc


class RecurringWheelHotfixTests(unittest.TestCase):
    def test_risen_phrase_is_availability_not_draw_deadline(self) -> None:
        published = datetime(2026, 7, 15, 12, 17, 8, tzinfo=UTC)
        available_at, method = wheel_event_runtime.infer_availability(
            "Через 2 часа запущу колесо с фрибетами, пока регайтесь в него",
            published,
            bbvg_monitor_main.monitor._bbvg_original_deadline_parser,
        )
        self.assertEqual(available_at, published + timedelta(hours=2))
        self.assertIn("время открытия", method)
        deadline, _ = bbvg_monitor_main.monitor.infer_deadline(
            "Через 2 часа запущу колесо с фрибетами, пока регайтесь в него",
            published,
        )
        self.assertIsNone(deadline)

        draw, _ = bbvg_monitor_main.monitor.infer_deadline(
            "Запускаем колесо сейчас, итоги через 2 часа",
            published,
        )
        self.assertEqual(draw, published + timedelta(hours=2))

    def test_new_publication_releases_old_inactive_and_completed_markers(self) -> None:
        event_at = datetime(2026, 7, 15, 12, 56, 55, tzinfo=UTC)
        state = {
            "inactive_wheels": {
                "solotg": {
                    "marked_at": "2026-07-14T14:50:27+00:00",
                    "expires_at": "2026-08-13T14:50:27+00:00",
                }
            },
            "url_alerts": {
                "solotg": {"alerted_at": "2026-07-14T14:00:51+00:00"}
            },
            "recently_completed_wheels": {
                "solotg": {"removed_at": "2026-07-14T15:00:00+00:00"}
            },
        }
        removed = wheel_event_runtime.reset_stale_event_state(
            state, "solotg", event_at
        )
        self.assertNotIn("solotg", state["inactive_wheels"])
        self.assertNotIn("solotg", state["url_alerts"])
        self.assertNotIn("solotg", state["recently_completed_wheels"])
        self.assertIn("inactive_wheels", removed)

    def test_already_seen_recent_post_is_requeued_after_old_marker_bug(self) -> None:
        seen_at = datetime(2026, 7, 15, 12, 58, 23, tzinfo=UTC)
        state = {
            "active_wheels": {},
            "seen": {"new-solotg-post": seen_at.isoformat()},
            "inactive_wheels": {
                "solotg": {"marked_at": "2026-07-14T14:50:27+00:00"}
            },
            "recently_completed_wheels": {},
            "manual_deadlines": {},
        }
        stats = {
            "sources": {
                "kolesaBB": {
                    "recent_post_keys": {
                        "new-solotg-post": {
                            "wheel": "solotg",
                            "seen_at": seen_at.isoformat(),
                        }
                    }
                }
            }
        }
        recovered = wheel_event_runtime.recover_recent_events_from_seen(
            state,
            stats,
            current=seen_at + timedelta(hours=1),
        )
        self.assertEqual(recovered, ["new-solotg-post"])
        self.assertNotIn("new-solotg-post", state["seen"])
        self.assertNotIn("solotg", state["inactive_wheels"])

    def test_current_event_manual_time_is_kept_but_yesterday_time_is_ignored(self) -> None:
        message_at = datetime(2026, 7, 15, 12, 17, 8, tzinfo=UTC)
        entry = {
            "identifier": "risen",
            "message_date": message_at.isoformat(),
            "message_text": "Через 2 часа запущу колесо",
        }
        state = {
            "manual_deadlines": {
                "risen": {
                    "deadline": "2026-07-14T16:56:48+00:00",
                    "updated_at": "2026-07-14T16:55:49+00:00",
                }
            }
        }
        self.assertIsNone(
            bbvg_monitor_main.recover_deadline_manual_first(state, "risen", entry)
        )

        current_deadline = message_at + timedelta(hours=4)
        state["manual_deadlines"]["risen"] = {
            "deadline": current_deadline.isoformat(),
            "updated_at": (message_at + timedelta(minutes=1)).isoformat(),
        }
        self.assertEqual(
            bbvg_monitor_main.recover_deadline_manual_first(state, "risen", entry),
            current_deadline,
        )

    def test_new_risen_event_stays_active_with_available_at(self) -> None:
        runtime = bbvg_monitor_main.monitor
        published = datetime.now(UTC) + timedelta(minutes=1)
        message = runtime.Message(
            source="artemkef",
            message_id=1404,
            date=published,
            text=(
                "Через 2 часа запущу колесо с фрибетами, пока регайтесь в него\n"
                "https://betboom.ru/freestream/risen"
            ),
            message_url="https://telegram.me/artemkef/1404",
        )
        link = "https://betboom.ru/freestream/risen"
        state = {
            "active_wheels": {},
            "inactive_wheels": {},
            "recently_completed_wheels": {
                "risen": {
                    "removed_at": "2026-07-14T17:01:20+00:00",
                    "expires_at": "2026-07-15T17:01:20+00:00",
                }
            },
            "manual_deadlines": {
                "risen": {
                    "deadline": "2026-07-14T16:56:48+00:00",
                    "updated_at": "2026-07-14T16:55:49+00:00",
                }
            },
            "completed_wheel_alerts": {
                "risen": {"notified_at": "2026-07-14T17:01:19+00:00"}
            },
            "url_alerts": {
                "risen": {"alerted_at": "2026-07-14T16:32:54+00:00"}
            },
            "activation_alerts": {},
            "participating_wheels": {},
            "pending_posts": {},
            "seen": {},
            "wheel_publications": {},
        }
        original_inspector = runtime.inspect_wheel_page
        runtime.inspect_wheel_page = lambda url: runtime.WheelInspection(
            "unknown", None, "страница без таймера"
        )
        try:
            assessment = runtime.assess_new_wheel(message, link, state)
            runtime.remember_pending(
                state,
                runtime.notification_key(message, link),
                message,
                link,
                assessment.status,
                assessment.method,
                initial_notified=True,
            )
        finally:
            runtime.inspect_wheel_page = original_inspector

        entry = state["active_wheels"]["risen"]
        self.assertEqual(assessment.status, "scheduled_availability")
        self.assertIsNone(assessment.deadline)
        self.assertNotIn("deadline", entry)
        self.assertEqual(
            runtime.parse_datetime(entry["available_at"]),
            published + timedelta(hours=2),
        )
        self.assertEqual(entry["availability_status"], "scheduled")
        self.assertNotIn("risen", state["manual_deadlines"])
        self.assertNotIn("risen", state["recently_completed_wheels"])

    def test_same_action_id_never_repeats_even_after_link_window(self) -> None:
        runtime = bbvg_monitor_main.monitor
        current = datetime.now(UTC)
        message = runtime.Message(
            source="collector",
            message_id=200,
            date=current,
            text="https://betboom.ru/freestream/reused",
            message_url="https://telegram.me/collector/200",
        )
        state = {
            "active_wheels": {},
            "inactive_wheels": {},
            "recently_completed_wheels": {},
            "wheel_action_history": {
                "reused": {
                    "action_id": 100,
                    "seen_at": (current - timedelta(days=1)).isoformat(),
                }
            },
        }
        original_inspector = runtime.inspect_wheel_page
        runtime.inspect_wheel_page = lambda url: runtime.WheelInspection(
            "active",
            current + timedelta(hours=1),
            "confirmed",
            action_id=100,
            verification_status=runtime.WHEEL_VERIFICATION_CONFIRMED,
        )
        try:
            result = runtime.assess_new_wheel(
                message, "https://betboom.ru/freestream/reused", state
            )
        finally:
            runtime.inspect_wheel_page = original_inspector
        self.assertFalse(result.should_notify)
        self.assertEqual(result.status, "duplicate_action")

    def test_new_action_id_releases_old_timer_immediately(self) -> None:
        runtime = bbvg_monitor_main.monitor
        current = datetime.now(UTC)
        message = runtime.Message(
            source="creator",
            message_id=201,
            date=current,
            text="https://betboom.ru/freestream/reused",
            message_url="https://telegram.me/creator/201",
        )
        state = {
            "active_wheels": {
                "reused": {
                    "action_id": 100,
                    "deadline": (current + timedelta(hours=8)).isoformat(),
                    "first_notified_at": current.isoformat(),
                }
            },
            "wheel_action_history": {
                "reused": {"action_id": 100, "seen_at": current.isoformat()}
            },
            "participating_wheels": {"reused": {"marked_at": current.isoformat()}},
            "url_alerts": {"reused": {"alerted_at": current.isoformat()}},
            "activation_alerts": {},
            "manual_deadlines": {},
            "manual_overrides": {},
            "wheel_publications": {"reused": [{"source": "old"}]},
            "inactive_wheels": {},
            "recently_completed_wheels": {},
        }
        original_inspector = runtime.inspect_wheel_page
        runtime.inspect_wheel_page = lambda url: runtime.WheelInspection(
            "active",
            current + timedelta(hours=2),
            "confirmed",
            action_id=101,
            verification_status=runtime.WHEEL_VERIFICATION_CONFIRMED,
        )
        try:
            result = runtime.assess_new_wheel(
                message, "https://betboom.ru/freestream/reused", state
            )
        finally:
            runtime.inspect_wheel_page = original_inspector
        self.assertTrue(result.should_notify)
        self.assertEqual(result.action_id, 101)
        self.assertNotIn("reused", state["active_wheels"])
        self.assertNotIn("reused", state["participating_wheels"])
        self.assertNotIn("reused", state["wheel_publications"])

    def test_api_failure_uses_legacy_two_hour_link_window(self) -> None:
        runtime = bbvg_monitor_main.monitor
        current = datetime.now(UTC)
        message = runtime.Message(
            source="collector",
            message_id=202,
            date=current,
            text="https://betboom.ru/freestream/noidentity",
            message_url="https://telegram.me/collector/202",
        )
        original_inspector = runtime.inspect_wheel_page
        runtime.inspect_wheel_page = lambda url: runtime.WheelInspection(
            "verification_failed",
            None,
            "temporary failure",
            verification_status=runtime.WHEEL_VERIFICATION_FAILED,
        )
        try:
            blocked_state = {
                "active_wheels": {
                    "noidentity": {
                        "first_notified_at": (current - timedelta(minutes=119)).isoformat()
                    }
                }
            }
            blocked = runtime.assess_new_wheel(
                message, "https://betboom.ru/freestream/noidentity", blocked_state
            )
            released_state = {
                "active_wheels": {
                    "noidentity": {
                        "first_notified_at": (current - timedelta(minutes=121)).isoformat()
                    }
                }
            }
            released = runtime.assess_new_wheel(
                message, "https://betboom.ru/freestream/noidentity", released_state
            )
        finally:
            runtime.inspect_wheel_page = original_inspector
        self.assertEqual(blocked.status, "duplicate_link")
        self.assertTrue(released.should_notify)
        self.assertEqual(released.status, "verification_failed")

    def test_availability_notification_is_sent_once_and_wheel_remains_active(self) -> None:
        current = datetime(2026, 7, 15, 14, 17, 9, tzinfo=UTC)
        sent: list[dict] = []

        class FakeMonitor:
            @staticmethod
            def now_utc():
                return current

            @staticmethod
            def parse_datetime(value):
                return wheel_event_runtime._parse_datetime(value)

            @staticmethod
            def active_entry_message(entry):
                return SimpleNamespace(
                    source=entry["source"],
                    message_id=entry["message_id"],
                    date=wheel_event_runtime._parse_datetime(entry["message_date"]),
                    text=entry["message_text"],
                    message_url=entry["message_url"],
                )

            @staticmethod
            def wheel_reply_markup(*args, **kwargs):
                return {"inline_keyboard": [[{"callback_data": "bb:p:event-token"}]]}

            @staticmethod
            def send_message(text, **kwargs):
                sent.append({"text": text, **kwargs})
                return {"ok": True}

        state = {
            "active_wheels": {
                "risen": {
                    "identifier": "risen",
                    "source": "artemkef",
                    "message_id": 1404,
                    "message_date": "2026-07-15T12:17:08+00:00",
                    "message_text": "Через 2 часа запущу колесо",
                    "message_url": "https://telegram.me/artemkef/1404",
                    "url": "https://betboom.ru/freestream/risen",
                    "available_at": "2026-07-15T14:17:08+00:00",
                    "availability_status": "scheduled",
                }
            }
        }
        first = wheel_event_runtime.process_due_availability(FakeMonitor, state)
        second = wheel_event_runtime.process_due_availability(FakeMonitor, state)
        self.assertEqual(first["availability_notifications"], 1)
        self.assertEqual(second["availability_notifications"], 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("доступно для участия", sent[0]["text"])
        self.assertIn("risen", state["active_wheels"])
        self.assertNotIn("deadline", state["active_wheels"]["risen"])

    def test_notification_dedup_is_scoped_to_publication_and_phase(self) -> None:
        text = (
            "🟡 Новое колесо BetBoom — участие откроется позже\n"
            "Идентификатор: <code>risen</code>"
        )
        first = notification_router.notification_event_identity(
            "wheels",
            text,
            "https://betboom.ru/freestream/risen",
            {"inline_keyboard": [[{"callback_data": "bb:p:event-one"}]]},
        )
        duplicate = notification_router.notification_event_identity(
            "wheels",
            text + "\nИсточник: @second",
            "https://betboom.ru/freestream/risen",
            {"inline_keyboard": [[{"callback_data": "bb:p:event-one"}]]},
        )
        reused = notification_router.notification_event_identity(
            "wheels",
            text,
            "https://betboom.ru/freestream/risen",
            {"inline_keyboard": [[{"callback_data": "bb:p:event-two"}]]},
        )
        available = notification_router.notification_event_identity(
            "wheels",
            "🟢 Колесо BetBoom доступно для участия\n"
            "Идентификатор: <code>risen</code>",
            "https://betboom.ru/freestream/risen",
            {"inline_keyboard": [[{"callback_data": "bb:p:event-one"}]]},
        )
        self.assertEqual(first, duplicate)
        self.assertNotEqual(first, reused)
        self.assertNotEqual(first, available)

    def test_active_revalidation_removes_confirmed_inactive_wheel(self) -> None:
        runtime = bbvg_monitor_main.runtime
        current = datetime(2026, 7, 16, 17, 23, tzinfo=UTC)
        state = {
            "active_wheels": {
                "papa": {
                    "identifier": "papa",
                    "url": "https://betboom.ru/freestream/papa",
                    "action_id": 852,
                }
            },
            "participating_wheels": {
                "papa": {"marked_at": current.isoformat()}
            },
            "button_contexts": {
                "token": {"wheel_key": "papa"}
            },
            "wheel_action_history": {},
        }
        original = runtime.monitor.inspect_wheel_page
        runtime.monitor.inspect_wheel_page = lambda url: runtime.monitor.WheelInspection(
            "inactive",
            current - timedelta(minutes=1),
            "confirmed inactive",
            action_id=852,
            verification_status=runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
        )
        try:
            result = runtime.revalidate_active_wheels(state, current)
        finally:
            runtime.monitor.inspect_wheel_page = original

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertNotIn("papa", state["active_wheels"])
        self.assertNotIn("papa", state["participating_wheels"])
        self.assertNotIn("token", state["button_contexts"])
        self.assertEqual(state["wheel_action_history"]["papa"]["action_id"], 852)

    def test_active_revalidation_updates_identity_but_keeps_manual_time(self) -> None:
        runtime = bbvg_monitor_main.runtime
        current = datetime(2026, 7, 16, 17, 23, tzinfo=UTC)
        message_at = current - timedelta(hours=1)
        manual_deadline = current + timedelta(hours=3)
        api_deadline = current + timedelta(hours=1)
        state = {
            "active_wheels": {
                "cct1": {
                    "identifier": "cct1",
                    "url": "https://betboom.ru/freestream/cct1",
                    "message_date": message_at.isoformat(),
                    "first_notified_at": message_at.isoformat(),
                }
            },
            "manual_deadlines": {
                "cct1": {
                    "deadline": manual_deadline.isoformat(),
                    "updated_at": current.isoformat(),
                }
            },
            "wheel_action_history": {},
        }
        original = runtime.monitor.inspect_wheel_page
        runtime.monitor.inspect_wheel_page = lambda url: runtime.monitor.WheelInspection(
            "active",
            api_deadline,
            "confirmed active",
            action_id=837,
            verification_status=runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
        )
        try:
            result = runtime.revalidate_active_wheels(state, current)
        finally:
            runtime.monitor.inspect_wheel_page = original

        entry = state["active_wheels"]["cct1"]
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(entry["action_id"], 837)
        self.assertEqual(entry["deadline"], manual_deadline.isoformat())
        self.assertEqual(entry["deadline_source"], "manual")
        self.assertEqual(entry["verification_status"], "confirmed")

    def test_active_revalidation_failure_keeps_wheel_with_warning(self) -> None:
        runtime = bbvg_monitor_main.runtime
        current = datetime(2026, 7, 16, 17, 23, tzinfo=UTC)
        state = {
            "active_wheels": {
                "zonertg7": {
                    "identifier": "zonertg7",
                    "url": "https://betboom.ru/freestream/zonertg7",
                    "deadline": (current + timedelta(hours=1)).isoformat(),
                }
            }
        }
        original = runtime.monitor.inspect_wheel_page
        runtime.monitor.inspect_wheel_page = lambda url: runtime.monitor.WheelInspection(
            "verification_failed",
            None,
            "temporary API failure",
            verification_status=runtime.monitor.WHEEL_VERIFICATION_FAILED,
        )
        try:
            result = runtime.revalidate_active_wheels(state, current)
        finally:
            runtime.monitor.inspect_wheel_page = original

        self.assertEqual(result["failed"], 1)
        self.assertIn("zonertg7", state["active_wheels"])
        entry = state["active_wheels"]["zonertg7"]
        self.assertEqual(entry["verification_status"], "failed")
        self.assertEqual(entry["last_verification_error"], "temporary API failure")

    def test_confirmed_api_without_timer_does_not_restore_telegram_timer(self) -> None:
        runtime = bbvg_monitor_main.runtime
        current = datetime(2026, 7, 16, 17, 23, tzinfo=UTC)
        message_at = current - timedelta(minutes=10)
        state = {
            "active_wheels": {
                "untimed": {
                    "identifier": "untimed",
                    "url": "https://betboom.ru/freestream/untimed",
                    "message_date": message_at.isoformat(),
                    "message_text": "Прокрутка через 2 часа",
                    "first_notified_at": message_at.isoformat(),
                }
            }
        }
        original = runtime.monitor.inspect_wheel_page
        runtime.monitor.inspect_wheel_page = lambda url: runtime.monitor.WheelInspection(
            "active",
            None,
            "confirmed without timer",
            action_id=900,
            verification_status=runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
        )
        try:
            runtime.revalidate_active_wheels(state, current)
        finally:
            runtime.monitor.inspect_wheel_page = original

        entry = state["active_wheels"]["untimed"]
        self.assertNotIn("deadline", entry)
        self.assertEqual(entry["deadline_source"], "api_missing")
        self.assertIsNone(
            bbvg_monitor_main.recover_deadline_manual_first(
                state, "untimed", entry
            )
        )

    def test_active_revalidation_new_action_clears_previous_participation(self) -> None:
        runtime = bbvg_monitor_main.runtime
        current = datetime(2026, 7, 16, 17, 23, tzinfo=UTC)
        state = {
            "active_wheels": {
                "reused": {
                    "identifier": "reused",
                    "url": "https://betboom.ru/freestream/reused",
                    "action_id": 100,
                    "participating": True,
                    "first_notified_at": (current - timedelta(hours=1)).isoformat(),
                }
            },
            "participating_wheels": {
                "reused": {"marked_at": current.isoformat()}
            },
            "manual_deadlines": {
                "reused": {
                    "deadline": (current + timedelta(hours=4)).isoformat(),
                    "updated_at": current.isoformat(),
                }
            },
            "wheel_action_history": {
                "reused": {"action_id": 100, "seen_at": current.isoformat()}
            },
        }
        original = runtime.monitor.inspect_wheel_page
        runtime.monitor.inspect_wheel_page = lambda url: runtime.monitor.WheelInspection(
            "active",
            current + timedelta(hours=2),
            "new action",
            action_id=101,
            verification_status=runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
        )
        try:
            runtime.revalidate_active_wheels(state, current)
        finally:
            runtime.monitor.inspect_wheel_page = original

        entry = state["active_wheels"]["reused"]
        self.assertEqual(entry["action_id"], 101)
        self.assertFalse(entry["participating"])
        self.assertNotIn("reused", state["participating_wheels"])
        self.assertNotIn("reused", state["manual_deadlines"])


if __name__ == "__main__":
    unittest.main()
