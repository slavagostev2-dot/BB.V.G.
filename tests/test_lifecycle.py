from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_queue
import monitor
import monitor_data
import rating_policy
import recurring_wheel_events
import wheel_publications_v2


UTC = timezone.utc


class WheelApiVerificationTests(unittest.TestCase):
    class Response:
        status_code = 200

        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def json(self) -> dict[str, Any]:
            return self.payload

        def raise_for_status(self) -> None:
            return None

    def setUp(self) -> None:
        self.original_request = monitor.request_with_retries
        self.original_now = monitor.now_utc
        self.current = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
        monitor.now_utc = lambda: self.current

    def tearDown(self) -> None:
        monitor.request_with_retries = self.original_request
        monitor.now_utc = self.original_now

    def response(self, info: dict[str, Any]) -> None:
        monitor.request_with_retries = lambda *args, **kwargs: self.Response(
            {"code": 200, "status": "OK", "info": info}
        )

    def test_active_action_uses_api_deadline_and_identity(self) -> None:
        self.response(
            {
                "action_id": 692,
                "start_dttm": "2026-07-16T14:00:00Z",
                "duration_min": 600,
                "is_ended": False,
                "is_early": False,
            }
        )
        result = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/zonertg7"
        )
        self.assertEqual(result.status, "active")
        self.assertEqual(result.action_id, 692)
        self.assertEqual(result.deadline, datetime(2026, 7, 17, 0, 0, tzinfo=UTC))
        self.assertEqual(result.verification_status, "confirmed")

    def test_expired_timer_wins_even_when_is_ended_is_false(self) -> None:
        self.response(
            {
                "action_id": 866,
                "start_dttm": "2026-07-16T14:26:28Z",
                "duration_min": 15,
                "is_ended": False,
                "is_early": False,
            }
        )
        result = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/kyzko"
        )
        self.assertEqual(result.status, "inactive")
        self.assertEqual(result.action_id, 866)

    def test_not_found_is_a_silent_definitive_rejection(self) -> None:
        monitor.request_with_retries = lambda *args, **kwargs: self.Response(
            {
                "code": 400,
                "status": "BAD_REQUEST",
                "error": {"message": "Акция не найдена"},
            }
        )
        message = monitor.Message(
            "source",
            1,
            self.current,
            "https://betboom.ru/freestream/missing",
            "https://telegram.me/source/1",
        )
        result = monitor.assess_new_wheel(
            message, "https://betboom.ru/freestream/missing", {}
        )
        self.assertFalse(result.should_notify)
        self.assertEqual(result.status, "inactive")

    def test_transport_failure_is_visible_as_unverified(self) -> None:
        def fail(*args: Any, **kwargs: Any):
            raise monitor.requests.Timeout("simulated timeout")

        monitor.request_with_retries = fail
        message = monitor.Message(
            "source",
            2,
            self.current,
            "https://betboom.ru/freestream/unverified",
            "https://telegram.me/source/2",
        )
        result = monitor.assess_new_wheel(
            message, "https://betboom.ru/freestream/unverified", {}
        )
        self.assertTrue(result.should_notify)
        self.assertEqual(result.status, "verification_failed")
        self.assertEqual(result.verification_status, "failed")


class WheelLifecycleTests(unittest.TestCase):
    def test_publications_from_two_channels_are_kept_once_each(self) -> None:
        rows = [
            {
                "source": "@Mechanogun",
                "message_id": "10",
                "message_date": "2026-07-15T10:00:00+00:00",
                "message_url": "https://telegram.me/mechanogun/10",
            },
            {
                "source": "collector",
                "message_id": 20,
                "message_date": "2026-07-15T10:01:00+00:00",
                "message_url": "https://telegram.me/collector/20",
            },
        ]
        merged = wheel_publications_v2.merge_publications(rows, [dict(rows[0])])
        self.assertEqual(len(merged), 2)
        self.assertEqual(
            wheel_publications_v2.publication_sources(
                {"wheel_publications": {"wheel-a": merged}}, "WHEEL-A"
            ),
            ["Mechanogun", "collector"],
        )

    def test_closed_event_is_pruned_but_newer_reuse_is_allowed(self) -> None:
        closed_at = datetime.now(UTC) - timedelta(days=2)
        old = [
            {
                "source": "mechanogun",
                "message_id": 1,
                "message_date": (closed_at - timedelta(minutes=1)).isoformat(),
                "message_url": "https://telegram.me/mechanogun/1",
            }
        ]
        state = {
            "active_wheels": {},
            "inactive_wheels": {},
            "recently_completed_wheels": {
                "reused": {"removed_at": closed_at.isoformat()}
            },
            "wheel_publications": {"reused": old},
        }
        self.assertTrue(
            wheel_publications_v2.closed_event_blocks_publications(state, "reused", old)
        )
        self.assertEqual(wheel_publications_v2.prune_closed_publications(state), 1)
        current = [
            {
                "source": "collector",
                "message_id": 2,
                "message_date": (closed_at + timedelta(days=1)).isoformat(),
            }
        ]
        self.assertFalse(
            wheel_publications_v2.closed_event_blocks_publications(
                state, "reused", current
            )
        )

    def test_reused_freestream_identifier_selects_current_event(self) -> None:
        recurring_wheel_events.self_test()

    def test_admin_confirmation_and_inactive_reversal_are_idempotent(self) -> None:
        stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}

        def decide(verdict: str) -> bool:
            return rating_policy.record_admin_wheel_decision(
                stats,
                wheel_key="wheel-a",
                sources=["mechanogun", "collector"],
                decision=verdict,
                actor="admin",
                at=datetime.now(UTC),
                recorder=monitor_data.record_admin_wheel_decision,
            )

        self.assertTrue(decide("confirmed"))
        self.assertFalse(decide("confirmed"))
        self.assertTrue(decide("inactive"))
        self.assertFalse(decide("inactive"))
        for source in ("mechanogun", "collector"):
            self.assertEqual(stats["sources"][source]["quality_score"], 0)
            self.assertGreaterEqual(stats["sources"][source]["quality_score"], 0)

    def test_admin_queue_applies_one_command_exactly_once(self) -> None:
        queue, command_id = admin_action_queue.append_command(
            admin_action_queue.default_queue(),
            "mark_inactive_global",
            "wheel-a|private-user-id",
            command_id="chapter3-idempotent",
        )
        self.assertNotIn("private-user-id", str(queue))
        state: dict[str, Any] = {
            "active_wheels": {"wheel-a": {"identifier": "wheel-a", "source": "one"}},
            "participating_wheels": {},
        }
        health: dict[str, Any] = {"sources": {}}
        stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
        first = admin_action_queue.process_pending(state, health, stats, queue=queue)
        second = admin_action_queue.process_pending(state, health, stats, queue=queue)
        self.assertEqual(first["applied"], 1)
        self.assertEqual(second["applied"], 0)
        self.assertIn(command_id, state["applied_admin_actions"])

    def test_failed_admin_action_is_recorded_and_retried(self) -> None:
        queue, command_id = admin_action_queue.append_command(
            admin_action_queue.default_queue(),
            "recheck_wheel",
            "wheel-a",
            command_id="chapter3-retry-action",
        )
        original = admin_action_queue.admin_action_v3.apply_action_v3
        attempts = 0

        def flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("simulated repository delay")
            return {
                "state_changed": True,
                "health_changed": False,
                "stats_changed": False,
            }

        admin_action_queue.admin_action_v3.apply_action_v3 = flaky
        state: dict[str, Any] = {}
        try:
            first = admin_action_queue.process_pending(state, {}, {}, queue=queue)
            second = admin_action_queue.process_pending(state, {}, {}, queue=queue)
            third = admin_action_queue.process_pending(state, {}, {}, queue=queue)
        finally:
            admin_action_queue.admin_action_v3.apply_action_v3 = original
        self.assertEqual((first["failed"], second["applied"], third["applied"]), (1, 1, 0))
        self.assertEqual(state["admin_action_results"][command_id]["status"], "applied")
        self.assertNotIn(command_id, state["admin_action_attempts"])

    def test_existing_publication_contracts(self) -> None:
        wheel_publications_v2.self_test()

    def test_publication_install_persists_before_duplicate_check(self) -> None:
        publication = {
            "source": "collector",
            "message_id": 5,
            "message_date": datetime.now(UTC).isoformat(),
            "message_url": "https://telegram.me/collector/5",
        }
        base = SimpleNamespace()
        base._WHEEL_PUBLICATIONS = {"wheel-a": [publication]}

        def original_persist(
            state: dict[str, Any], key: str, fallback: dict[str, Any] | None = None
        ) -> None:
            rows = list(base._WHEEL_PUBLICATIONS.get(key, []))
            if not rows and fallback:
                rows = [fallback]
            if rows:
                state.setdefault("wheel_publications", {})[key] = rows

        base._persist_publications = original_persist
        monitor = SimpleNamespace(
            wheel_key=lambda link: link.rsplit("/", 1)[-1].split("?", 1)[0].casefold(),
            is_suppressed=lambda state, link: True,
            is_activation_suppressed=lambda state, link: False,
            load_state=lambda: {
                "active_wheels": {},
                "inactive_wheels": {},
                "recently_completed_wheels": {
                    "closed": {"removed_at": datetime.now(UTC).isoformat()}
                },
                "wheel_publications": {"closed": [publication]},
            },
        )
        wheel_publications_v2.install(monitor, SimpleNamespace(base_runtime=base))
        state = {
            "active_wheels": {
                "wheel-a": {"identifier": "wheel-a", "source": "mechanogun"}
            },
            "inactive_wheels": {},
            "recently_completed_wheels": {},
            "wheel_publications": {},
        }
        self.assertTrue(
            monitor.is_suppressed(state, "https://betboom.ru/freestream/wheel-a")
        )
        self.assertEqual(state["active_wheels"]["wheel-a"]["sources"], ["collector", "mechanogun"])
        self.assertFalse(
            monitor.is_activation_suppressed(
                state, "https://betboom.ru/freestream/wheel-a"
            )
        )
        loaded = monitor.load_state()
        self.assertNotIn("closed", loaded["wheel_publications"])


if __name__ == "__main__":
    unittest.main()
