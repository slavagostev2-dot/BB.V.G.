from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_queue
import admin_panel_v2
import bbvg_monitor_main
import incident_manager
import personal_wheel_voting
import system_checks
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41


class Chapter3BehavioralContractTests(unittest.TestCase):
    def test_health_accepts_candidates_added_after_intelligence_scan(self) -> None:
        now = datetime.now(timezone.utc)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "public_sources.txt"
            nightly = root / "source_catalog.txt"
            discovery = root / "discovery_state.json"
            intelligence = root / "intelligence_state.json"
            primary.write_text("alpha\nbeta\n", encoding="utf-8")
            nightly.write_text("gamma\ndelta\n", encoding="utf-8")
            discovery.write_text(
                json.dumps({
                    "telegram_domain": "telegram.me",
                    "active_size": 2,
                    "catalog_size": 2,
                    "intelligence_candidates_added": 2,
                    "error_count": 0,
                    "last_run_at": now.isoformat(),
                }),
                encoding="utf-8",
            )
            intelligence.write_text(
                json.dumps({
                    "telegram_domain": "telegram.me",
                    "last_run_at": now.isoformat(),
                    "last_run_summary": {
                        "known_sources": 2,
                        "sources_scanned": 2,
                        "errors": 0,
                    },
                }),
                encoding="utf-8",
            )
            original = (
                system_checks.PUBLIC_SOURCES_PATH,
                system_checks.NIGHTLY_SOURCES_PATH,
                system_checks.DISCOVERY_PATH,
                system_checks.INTELLIGENCE_PATH,
            )
            try:
                system_checks.PUBLIC_SOURCES_PATH = primary
                system_checks.NIGHTLY_SOURCES_PATH = nightly
                system_checks.DISCOVERY_PATH = discovery
                system_checks.INTELLIGENCE_PATH = intelligence
                findings: list[dict] = []
                system_checks.check_discovery_runtime({}, findings)
            finally:
                (
                    system_checks.PUBLIC_SOURCES_PATH,
                    system_checks.NIGHTLY_SOURCES_PATH,
                    system_checks.DISCOVERY_PATH,
                    system_checks.INTELLIGENCE_PATH,
                ) = original
            self.assertFalse(
                any(row["kind"] == "discovery_scan_failure" for row in findings)
            )

    def test_single_getupdates_consumer_by_behavior(self) -> None:
        monitor_calls: list[str] = []
        original_api = bbvg_monitor_main.monitor.telegram_api
        try:
            bbvg_monitor_main.monitor.telegram_api = (
                lambda method, payload: monitor_calls.append(method)
                or {"ok": True, "result": []}
            )
            result = bbvg_monitor_main.monitor.process_bot_feedback({}, {}, {})
        finally:
            bbvg_monitor_main.monitor.telegram_api = original_api
        self.assertEqual(result, {"callbacks": 0, "participating": 0, "lists": 0})
        self.assertEqual(monitor_calls, [])

        panel = TelegramPanelRuntimeV41()
        panel_calls: list[str] = []
        panel.load_access = lambda force=False: None
        panel.setup_bot = lambda: None
        panel.record_runtime_heartbeat = lambda force=False: None
        panel.telegram_api = (
            lambda method, payload: panel_calls.append(method)
            or {"ok": True, "result": []}
        )

        class NoThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self) -> None:
                return None

        with (
            patch.object(admin_panel_v2.legacy, "BOT_TOKEN", "test"),
            patch.object(admin_panel_v2.legacy, "BOT_CHAT_ID", "1"),
            patch.object(admin_panel_v2.legacy, "GITHUB_TOKEN", "test"),
            patch.object(admin_panel_v2.legacy, "GITHUB_REPOSITORY", "owner/repo"),
            patch.object(admin_panel_v2.legacy, "RUN_SECONDS", 1),
            patch.object(admin_panel_v2.threading, "Thread", NoThread),
            patch.object(admin_panel_v2.time, "monotonic", side_effect=[0, 0, 2]),
        ):
            self.assertEqual(panel.run(), 0)
        self.assertEqual(panel_calls, ["getUpdates"])

    def test_current_inventory_transport_and_stale_incident_resolution(self) -> None:
        fixed = datetime(2026, 7, 17, 13, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public_sources.txt"
            nightly = root / "source_catalog.txt"
            transport = root / "source_transport_state.json"
            incidents = root / "incident_state.json"
            public.write_text("alpha\nbeta\n", encoding="utf-8")
            nightly.write_text("gamma\n", encoding="utf-8")
            original_paths = (
                system_checks.PUBLIC_SOURCES_PATH,
                system_checks.NIGHTLY_SOURCES_PATH,
                system_checks.SOURCE_TRANSPORT_STATE_PATH,
                incident_manager.STATE_PATH,
            )
            original_now = system_checks.now_utc
            try:
                system_checks.PUBLIC_SOURCES_PATH = public
                system_checks.NIGHTLY_SOURCES_PATH = nightly
                system_checks.SOURCE_TRANSPORT_STATE_PATH = transport
                incident_manager.STATE_PATH = incidents
                system_checks.now_utc = lambda: fixed
                transport.write_text(json.dumps({
                    "status": "success", "domain": "telegram.me",
                    "checked_at": (fixed - timedelta(hours=40)).isoformat(),
                    "configured_sources": 2, "primary_sources": 2,
                    "nightly_sources": 0, "accounted_sources": 2,
                    "error_sources": 0, "missing_sources": ["gamma"],
                }), encoding="utf-8")
                findings: list[dict] = []
                details: dict = {}
                system_checks.check_inventory(details, findings)
                system_checks.check_automation_state(details, findings)
                self.assertEqual(details["inventory"]["primary_operational"], 2)
                self.assertEqual(details["inventory"]["nightly_operational"], 1)
                self.assertEqual(details["inventory"]["total"], 3)
                self.assertTrue(any(row["kind"] == "source_transport_smoke" for row in findings))
                self.assertTrue(any(row["kind"] == "source_transport_stale" for row in findings))
                incident_manager.reconcile(findings, scope=system_checks.SCOPE)
                transport.write_text(json.dumps({
                    "status": "success", "domain": "telegram.me",
                    "checked_at": fixed.isoformat(), "configured_sources": 3,
                    "primary_sources": 2, "nightly_sources": 1,
                    "accounted_sources": 3, "reachable_sources": 3,
                    "error_sources": 0, "missing_sources": [],
                }), encoding="utf-8")
                recovered: list[dict] = []
                recovered_details: dict = {}
                system_checks.check_inventory(recovered_details, recovered)
                system_checks.check_automation_state(recovered_details, recovered)
                self.assertFalse(any(row["kind"].startswith("source_transport_") for row in recovered))
                state = incident_manager.reconcile(recovered, scope=system_checks.SCOPE)
                transport_rows = [
                    row for row in state["incidents"].values()
                    if row["kind"].startswith("source_transport_")
                ]
                self.assertTrue(transport_rows)
                self.assertTrue(all(row["status"] == "resolved" for row in transport_rows))
            finally:
                (
                    system_checks.PUBLIC_SOURCES_PATH,
                    system_checks.NIGHTLY_SOURCES_PATH,
                    system_checks.SOURCE_TRANSPORT_STATE_PATH,
                    incident_manager.STATE_PATH,
                ) = original_paths
                system_checks.now_utc = original_now

    def test_every_admin_command_is_queue_idempotent(self) -> None:
        actor = personal_wheel_voting.actor_vote_token("42", secret="chapter3-test")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        commands = {
            "participate_token": "token-1", "participate_wheel": "wheel-a",
            "record_personal_vote": json.dumps({
                "wheel_key": "wheel-a", "event_key": "wheel-a#action:1",
                "actor": actor, "role": "user", "weight": 1,
                "sources": ["source-a"],
            }),
            "mark_inactive_global": "wheel-a|admin",
            "confirm_finished_global": "wheel-a|admin",
            "set_deadline": f"wheel-a|{future}", "remove_active": "wheel-a",
            "recheck_wheel": "wheel-a", "clear_quarantine": "source-a",
        }
        self.assertEqual(set(commands), admin_action_queue.ALLOWED_ACTIONS)
        for index, (action, value) in enumerate(commands.items(), start=1):
            with self.subTest(action=action):
                state = {
                    "active_wheels": {"wheel-a": {
                        "identifier": "wheel-a",
                        "url": "https://betboom.ru/freestream/wheel-a",
                        "source": "source-a",
                        "message_date": datetime.now(timezone.utc).isoformat(),
                        "event_id": "event-a",
                    }},
                    "button_contexts": {"token-1": {
                        "wheel_key": "wheel-a", "identifier": "wheel-a",
                        "url": "https://betboom.ru/freestream/wheel-a",
                        "source": "source-a",
                    }},
                    "wheel_publications": {"wheel-a": [{"source": "source-a"}]},
                    "participating_wheels": {}, "pending_posts": {},
                }
                health = {"sources": {"source-a": {
                    "status": "quarantined", "consecutive_errors": 3,
                    "last_error": "test",
                }}}
                stats = {"version": 1, "sources": {}, "daily": {}}
                queue, command_id = admin_action_queue.append_command(
                    admin_action_queue.default_queue(), action, value,
                    command_id=f"chapter3-{index:02d}-command",
                )
                first = admin_action_queue.process_pending(state, health, stats, queue=queue)
                stable_snapshot = deepcopy((state, health, stats))
                second = admin_action_queue.process_pending(state, health, stats, queue=queue)
                self.assertEqual(first["applied"], 1)
                self.assertEqual(second["applied"], 0)
                self.assertIn(command_id, state["applied_admin_actions"])
                self.assertEqual((state, health, stats), stable_snapshot)


if __name__ == "__main__":
    unittest.main()
