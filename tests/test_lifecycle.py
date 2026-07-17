from __future__ import annotations

import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_queue
import incident_manager
import monitor
import monitor_data
import rating_policy
import recurring_wheel_events
import security_audit
import system_checks
import wheel_publications_v2
import wheel_lifecycle_v2


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

    def test_three_failed_checks_open_health_incident_and_success_recovers(self) -> None:
        state: dict[str, Any] = {}
        failed = monitor.WheelInspection(
            "verification_failed",
            None,
            "BetBoom API timeout",
            verification_status=monitor.WHEEL_VERIFICATION_FAILED,
        )
        success = monitor.WheelInspection(
            "active",
            self.current + timedelta(hours=1),
            "ok",
            verification_status=monitor.WHEEL_VERIFICATION_CONFIRMED,
        )
        for offset in range(3):
            monitor.record_wheel_api_verification(
                state, failed, checked_at=self.current + timedelta(minutes=offset)
            )
        self.assertEqual(state["wheel_api_health"]["status"], "degraded")
        self.assertEqual(state["wheel_api_health"]["consecutive_failures"], 3)

        original_runtime_path = system_checks.RUNTIME_STATE_PATH
        original_incident_path = incident_manager.STATE_PATH
        try:
            with TemporaryDirectory() as temporary:
                runtime_path = Path(temporary) / "state.json"
                incident_path = Path(temporary) / "incident_state.json"
                runtime_path.write_text(json.dumps(state), encoding="utf-8")
                system_checks.RUNTIME_STATE_PATH = runtime_path
                incident_manager.STATE_PATH = incident_path
                details: dict[str, Any] = {}
                findings: list[dict[str, Any]] = []
                system_checks.check_wheel_api_health(details, findings)
                self.assertEqual(
                    [item["kind"] for item in findings],
                    ["wheel_api_validation_failure"],
                )
                opened = incident_manager.reconcile(findings, scope="system_checks")
                self.assertEqual(len(incident_manager.pending_open(opened)), 1)
                key = incident_manager.pending_open(opened)[0]["key"]
                incident_manager.mark_notified([key], "open")
                sequence = opened["sequence"]
                last_change = opened["last_change_at"]
                monitor.record_wheel_api_verification(
                    state, failed, checked_at=self.current + timedelta(minutes=3)
                )
                runtime_path.write_text(json.dumps(state), encoding="utf-8")
                repeated_findings: list[dict[str, Any]] = []
                system_checks.check_wheel_api_health({}, repeated_findings)
                repeated = incident_manager.reconcile(
                    repeated_findings, scope="system_checks"
                )
                self.assertEqual(incident_manager.pending_open(repeated), [])
                self.assertEqual(repeated["sequence"], sequence)
                self.assertEqual(repeated["last_change_at"], last_change)

                monitor.record_wheel_api_verification(
                    state, success, checked_at=self.current + timedelta(minutes=4)
                )
                runtime_path.write_text(json.dumps(state), encoding="utf-8")
                recovered_findings: list[dict[str, Any]] = []
                system_checks.check_wheel_api_health({}, recovered_findings)
                self.assertEqual(recovered_findings, [])
                recovered = incident_manager.reconcile(
                    recovered_findings, scope="system_checks"
                )
                self.assertEqual(len(incident_manager.pending_resolved(recovered)), 1)
        finally:
            system_checks.RUNTIME_STATE_PATH = original_runtime_path
            incident_manager.STATE_PATH = original_incident_path

        self.assertEqual(state["wheel_api_health"]["status"], "ok")
        self.assertEqual(state["wheel_api_health"]["consecutive_failures"], 0)

    def test_untimed_wheel_expires_after_two_hours(self) -> None:
        self.assertEqual(
            monitor.participation_expiry(None, current=self.current),
            self.current + timedelta(hours=2),
        )


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


class PublicProvenancePrivacyTests(unittest.TestCase):
    def test_admin_writers_store_only_admin_provenance(self) -> None:
        current = datetime(2026, 7, 17, 3, 0, tzinfo=UTC)
        stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
        self.assertTrue(
            monitor_data.record_admin_wheel_decision(
                stats,
                wheel_key="wheel-a#action:10",
                sources=["source"],
                decision="confirmed",
                actor="123456789",
                at=current,
            )
        )
        decision = stats["admin_wheel_decisions"]["wheel-a#action:10"]
        self.assertEqual(decision["actor"], "admin")
        self.assertEqual(decision["decided_at"], current.isoformat())

        state: dict[str, Any] = {
            "active_wheels": {
                "wheel-a": {
                    "identifier": "wheel-a",
                    "event_id": "event-a",
                    "action_id": 10,
                }
            }
        }
        entry = state["active_wheels"]["wheel-a"]
        wheel_lifecycle_v2.mark_inactive_event(
            state,
            "wheel-a",
            entry,
            current=current,
            actor="123456789",
        )
        inactive = state["inactive_wheels"]["wheel-a"]
        self.assertEqual(inactive["marked_by"], "admin")
        self.assertEqual(inactive["action_id"], 10)
        self.assertEqual(inactive["event_id"], "event-a")
        self.assertEqual(inactive["marked_at"], current.isoformat())

    def test_current_json_migration_preserves_keys_timestamps_and_counters(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            stats_path = root / "source_stats.json"
            moderation_path = root / "candidate_moderation.json"
            state_path.write_text(
                json.dumps(
                    {
                        "inactive_wheels": {
                            "wheel-a": {
                                "marked_by": "123456789",
                                "marked_at": "2026-07-17T01:00:00+00:00",
                                "action_id": 10,
                            }
                        },
                        "recently_completed_wheels": {
                            "wheel-b": {
                                "confirmed_finished_by": "123456789",
                                "rating_event_key": "wheel-b#action:11",
                            }
                        },
                        "checks": 77,
                    }
                ),
                encoding="utf-8",
            )
            stats_path.write_text(
                json.dumps(
                    {
                        "admin_wheel_decisions": {
                            "wheel-b#action:11": {
                                "actor": "123456789",
                                "decision": "confirmed",
                                "decided_at": "2026-07-17T01:05:00+00:00",
                                "sources": ["source"],
                            }
                        },
                        "personal_wheel_votes": {
                            "event": {
                                "actor": "vote_" + "a" * 32,
                                "event_key": "wheel-b#action:11",
                            }
                        },
                        "checks": 88,
                    }
                ),
                encoding="utf-8",
            )
            moderation_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "ignored": {
                            "source": {
                                "ignored_by": "123456789",
                                "ignored_at": "2026-07-17T01:10:00+00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            changed = security_audit.migrate_current(
                [state_path, stats_path, moderation_path]
            )
            self.assertEqual(
                changed,
                ["state.json", "source_stats.json", "candidate_moderation.json"],
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            moderation = json.loads(moderation_path.read_text(encoding="utf-8"))
            self.assertEqual(state["inactive_wheels"]["wheel-a"]["marked_by"], "admin")
            self.assertEqual(
                state["recently_completed_wheels"]["wheel-b"]["confirmed_finished_by"],
                "admin",
            )
            self.assertEqual(state["checks"], 77)
            self.assertEqual(state["inactive_wheels"]["wheel-a"]["action_id"], 10)
            self.assertEqual(stats["admin_wheel_decisions"]["wheel-b#action:11"]["actor"], "admin")
            self.assertEqual(stats["checks"], 88)
            self.assertEqual(
                stats["personal_wheel_votes"]["event"]["actor"],
                "vote_" + "a" * 32,
            )
            self.assertEqual(moderation["ignored"]["source"]["ignored_by"], "admin")
            for path in (state_path, stats_path, moderation_path):
                self.assertEqual(security_audit._runtime_provenance_findings(path), [])


if __name__ == "__main__":
    unittest.main()
