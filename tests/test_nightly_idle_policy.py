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
import system_checks
from admin_panel_runtime_v5 import TelegramPanelRuntimeV5
from admin_panel_runtime_v14 import TelegramPanelRuntimeV14
from admin_panel_runtime_v17 import TelegramPanelRuntimeV17
from admin_panel_runtime_v38 import TelegramPanelRuntimeV38


ROOT = Path(__file__).resolve().parents[1]


class NightlyIdlePolicyTests(unittest.TestCase):
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
            TelegramPanelRuntimeV14.bulk_set_intelligence_mode,
            TelegramPanelRuntimeV17.decide_source_request,
        ):
            self.assertNotIn("nightly-discovery.yml", inspect.getsource(method))

    def test_catalog_change_does_not_trigger_nightly_workflow(self) -> None:
        workflow = (ROOT / ".github/workflows/nightly-discovery.yml").read_text(
            encoding="utf-8"
        )
        push_paths = workflow.split("workflow_dispatch:", 1)[0]
        self.assertNotIn('"source_catalog.txt"', push_paths)
        self.assertNotIn('"public_sources.txt"', push_paths)

    def test_empty_nightly_list_is_shown_as_idle_without_start_button(self) -> None:
        panel = TelegramPanelRuntimeV38()
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
