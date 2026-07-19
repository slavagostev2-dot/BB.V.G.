from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import nightly_discovery
import notification_router
import source_tier_maintenance
import system_checks
from admin_panel_runtime_v5 import TelegramPanelRuntimeV5
from bbvg.bot.source_requests import SourceRequestRuntime
from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.interface import PanelInterfaceRuntime


ROOT = Path(__file__).resolve().parents[1]


class NightlyIdlePolicyTests(unittest.TestCase):
    def test_automatic_promotion_notice_is_admin_only_and_auditable(self) -> None:
        text = nightly_discovery.promotion_admin_message(
            [
                {
                    "source": "StreamSource",
                    "identifier": "wheel-42",
                    "message_url": "https://telegram.me/StreamSource/123",
                }
            ]
        )

        self.assertEqual("admin_system", notification_router.notification_kind(text))
        self.assertIn("@StreamSource", text)
        self.assertIn("wheel-42", text)
        self.assertIn("https://telegram.me/StreamSource/123", text)
        self.assertIn("уже перенесён", text)

    def test_intelligence_cannot_automatically_populate_nightly_scan(self) -> None:
        module_source = inspect.getsource(nightly_discovery)
        self.assertFalse(hasattr(nightly_discovery, "load_intelligence_nightly_candidates"))
        self.assertFalse(hasattr(nightly_discovery, "intelligence_candidates_for_nightly"))
        self.assertNotIn("candidate_is_nightly_eligible", module_source)

    def test_completion_notice_requires_a_real_manual_scan(self) -> None:
        self.assertFalse(
            nightly_discovery.should_notify_completion(
                manual_run=True, catalog_size_at_start=0
            )
        )
        self.assertFalse(
            nightly_discovery.should_notify_completion(
                manual_run=False, catalog_size_at_start=3
            )
        )
        self.assertTrue(
            nightly_discovery.should_notify_completion(
                manual_run=True, catalog_size_at_start=3
            )
        )

    def test_candidate_changes_do_not_dispatch_a_nightly_scan(self) -> None:
        for method in (
            TelegramPanelRuntimeV5.set_candidate_mode,
            TelegramPanelRuntimeV5.restore_candidate,
            PanelInterfaceRuntime.bulk_set_intelligence_mode,
            SourceRequestRuntime.decide_source_request,
        ):
            self.assertNotIn("nightly-discovery.yml", inspect.getsource(method))

    def test_catalog_change_does_not_trigger_nightly_workflow(self) -> None:
        workflow = (ROOT / ".github/workflows/nightly-discovery.yml").read_text(
            encoding="utf-8"
        )
        push_paths = workflow.split("workflow_dispatch:", 1)[0]
        self.assertNotIn('"source_catalog.txt"', push_paths)
        self.assertNotIn('"public_sources.txt"', push_paths)

    def test_intelligence_run_does_not_feed_nightly_discovery(self) -> None:
        workflow = (ROOT / ".github/workflows/nightly-discovery.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('workflows: ["Telegram source intelligence"]', workflow)
        self.assertNotIn("github.event.workflow_run", workflow)

    def test_tier_audit_never_moves_sources_to_nightly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "public_sources.txt"
            nightly = root / "source_catalog.txt"
            stats = root / "source_stats.json"
            state = root / "source_tier_state.json"
            primary.write_text("eligible_channel\n", encoding="utf-8")
            nightly.write_text("manual_channel\n", encoding="utf-8")
            now = source_tier_maintenance.datetime.now(source_tier_maintenance.UTC)
            old = (now - source_tier_maintenance.timedelta(days=10)).isoformat()
            recent = (now - source_tier_maintenance.timedelta(minutes=5)).isoformat()
            daily = {}
            for offset in range(source_tier_maintenance.INACTIVITY_DAYS):
                day = (now.date() - source_tier_maintenance.timedelta(days=offset)).isoformat()
                daily[day] = {"sources": {"eligible_channel": {"successful_checks": 20}}}
            stats.write_text(
                json.dumps({
                    "sources": {"eligible_channel": {
                        "first_checked_at": old,
                        "last_checked_at": recent,
                        "successful_checks": 200,
                    }},
                    "daily": daily,
                }),
                encoding="utf-8",
            )
            original = (
                source_tier_maintenance.PRIMARY_PATH,
                source_tier_maintenance.NIGHTLY_PATH,
                source_tier_maintenance.STATS_PATH,
                source_tier_maintenance.STATE_PATH,
            )
            try:
                source_tier_maintenance.PRIMARY_PATH = primary
                source_tier_maintenance.NIGHTLY_PATH = nightly
                source_tier_maintenance.STATS_PATH = stats
                source_tier_maintenance.STATE_PATH = state
                self.assertEqual(source_tier_maintenance.main(), 0)
            finally:
                (
                    source_tier_maintenance.PRIMARY_PATH,
                    source_tier_maintenance.NIGHTLY_PATH,
                    source_tier_maintenance.STATS_PATH,
                    source_tier_maintenance.STATE_PATH,
                ) = original
            audit = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(primary.read_text(encoding="utf-8"), "eligible_channel\n")
            self.assertEqual(nightly.read_text(encoding="utf-8"), "manual_channel\n")
            self.assertEqual(audit["policy"], "manual_nightly_only")
            self.assertEqual(audit["moved_to_nightly"], [])
            self.assertEqual(audit["would_move_to_nightly"], ["eligible_channel"])

    def test_empty_nightly_list_is_shown_as_idle_without_start_button(self) -> None:
        panel = TelegramPanelRuntime()
        captured: list[tuple[str, dict]] = []
        panel.current_user_id = "1"
        panel.current_chat_id = "1"
        panel.current_role = "owner"
        panel.navigation = {"1": ["menu", "discovery"]}
        panel.is_admin = lambda: True  # type: ignore[method-assign]
        panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
            state={},
            stats={"sources": {}, "daily": {}},
            health={"sources": {}},
            discovery={"sources": {}},
            fast=["mechanogun"],
            nightly=[],
        )
        panel.candidate_rows = lambda: []  # type: ignore[method-assign]
        panel.workflow_run = lambda value: {  # type: ignore[method-assign]
            "status": "completed",
            "conclusion": "success",
        }
        panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]

        panel.show_discovery()

        text, kwargs = captured[-1]
        self.assertIn("не требуется — ночной список пуст", text)
        self.assertNotIn("control:nightly", str(kwargs.get("reply_markup")))
        self.assertIn("page:discovery", str(kwargs.get("reply_markup")))

    def test_nightly_source_waiting_for_first_scan_is_not_an_incident(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "public_sources.txt"
            nightly = root / "source_catalog.txt"
            health = root / "source_health.json"
            primary.write_text("primary_channel\n", encoding="utf-8")
            nightly.write_text("new_nightly_channel\n", encoding="utf-8")
            health.write_text(
                json.dumps(
                    {
                        "sources": {
                            "primary_channel": {
                                "status": "ok",
                                "checks": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            original_paths = (
                system_checks.PUBLIC_SOURCES_PATH,
                system_checks.NIGHTLY_SOURCES_PATH,
                system_checks.HEALTH_PATH,
            )
            try:
                system_checks.PUBLIC_SOURCES_PATH = primary
                system_checks.NIGHTLY_SOURCES_PATH = nightly
                system_checks.HEALTH_PATH = health
                details: dict = {}
                findings: list[dict] = []
                system_checks.check_source_health(details, findings)
            finally:
                (
                    system_checks.PUBLIC_SOURCES_PATH,
                    system_checks.NIGHTLY_SOURCES_PATH,
                    system_checks.HEALTH_PATH,
                ) = original_paths

            self.assertEqual([], findings)
            summary = details["source_health_summary"]
            self.assertEqual([], summary["missing_sources"])
            self.assertEqual(
                ["new_nightly_channel"],
                summary["nightly_pending_first_check"],
            )


if __name__ == "__main__":
    unittest.main()
