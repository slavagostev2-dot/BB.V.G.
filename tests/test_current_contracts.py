from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_v2
import admin_action_v3
import admin_action_queue
import admin_panel_runtime_v41
import admin_runtime
import bot_private_state
import incident_manager
import monitor_health
import notification_navigation
import notification_preferences_v2
import personal_reminder_filter
import privacy_retention
import source_intelligence
import source_intelligence_alerts
import source_registry
import system_checks_v2
import wheel_lifecycle_v2
import wheel_link_lifecycle
import wheel_metadata_quality
import wheel_scenario_suite
from bbvg.bot import interface as panel_interface
from bbvg.bot import runtime as panel_runtime
from bbvg.bot import sources as panel_sources
from bbvg.bot import users as panel_users


class CurrentProductionContractTests(unittest.TestCase):
    def test_source_intelligence_keeps_only_thematic_non_bot_references(self) -> None:
        noise = (
            "Техническая поддержка @wheel_helper_bot. "
            "Автор публикации @ordinaryperson."
        )
        self.assertEqual(source_intelligence.reference_candidates(noise), {})

        relevant = source_intelligence.reference_candidates(
            "Сегодня стрим и киберспортивный турнир у @RealCaster, "
            "регистрация через @tournament_helper_bot."
        )
        self.assertEqual(set(relevant), {"RealCaster"})
        self.assertIn("стримы", relevant["RealCaster"])
        self.assertIn("киберспорт и игры", relevant["RealCaster"])

    def test_indirect_verified_candidates_are_in_primary_inventory(self) -> None:
        root = Path(__file__).resolve().parents[1]
        primary = {
            value.casefold()
            for value in (root / "public_sources.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if value and not value.startswith("#")
        }
        expected = {
            "arszeeqq",
            "bettingmedialeague",
            "fishmandota2",
            "fonbetesports",
            "igmmlbb",
            "stavka_tv",
            "streamrosstg",
            "xdzachq",
        }
        self.assertTrue(expected.issubset(primary))
        # The Telegram administrator can promote newly verified candidates at
        # runtime, so the inventory may legitimately grow beyond the audited
        # baseline without requiring this contract to be rewritten.
        self.assertGreaterEqual(len(primary), 157)
        self.assertGreaterEqual(source_intelligence.SOURCE_LIMIT, 160)

        workflow = (
            root / ".github/workflows/activate-66-sources.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('"public_sources.txt"', workflow)
        self.assertIn('"source_catalog.txt"', workflow)
        self.assertNotIn("Check all 66 sources", workflow)
        self.assertIn(
            'workflows: ["Telegram candidate discovery"]', workflow
        )

        registry_workflow = (
            root / ".github/workflows/source-registry.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'workflows: ["Telegram candidate discovery"]', registry_workflow
        )

    def test_source_change_refreshes_every_runtime_consumer(self) -> None:
        bot = admin_runtime.RuntimeAdminBot()
        calls: list[tuple[str, dict[str, str] | None]] = []
        bot.dispatch = lambda workflow, inputs=None: calls.append(  # type: ignore[method-assign]
            (workflow, inputs)
        )

        self.assertEqual(bot.refresh_source_runtime(), [])

        self.assertEqual(calls, list(admin_runtime.SOURCE_REFRESH_WORKFLOWS))
        self.assertIn(
            ("monitor.yml", {"continuous": "true", "replace": "true"}),
            calls,
        )
        self.assertIn(("activate-66-sources.yml", None), calls)
        self.assertIn(("source-registry.yml", None), calls)

    def test_source_refresh_failure_does_not_hide_saved_source_change(self) -> None:
        bot = admin_runtime.RuntimeAdminBot()

        def dispatch(workflow: str, inputs: dict[str, str] | None = None) -> None:
            if workflow == "activate-66-sources.yml":
                raise RuntimeError("temporary GitHub failure")

        bot.dispatch = dispatch  # type: ignore[method-assign]

        self.assertEqual(
            bot.refresh_source_runtime(),
            ["activate-66-sources.yml"],
        )

    def test_administrator_decisions(self) -> None:
        admin_action_v2.self_test()
        admin_action_v3.self_test()

    def test_runtime_chain_contracts_used_by_v41(self) -> None:
        panel_interface.self_test()
        panel_users.self_test()
        panel_runtime.self_test()
        admin_panel_runtime_v41.self_test()

    def test_production_runtime_has_only_stable_panel_layers(self) -> None:
        runtime = panel_runtime.TelegramPanelRuntime
        self.assertFalse(
            [
                cls
                for cls in runtime.__mro__
                if cls.__module__.startswith("admin_panel_runtime_v")
            ]
        )
        self.assertEqual(len(runtime.__mro__), len(set(runtime.__mro__)))
        for method_name in (
            "handle_callback",
            "render_page",
            "show_active",
            "show_user_detail",
            "dispatch_admin_action",
            "setup_bot",
            "save_access",
        ):
            source = Path(inspect.getsourcefile(getattr(runtime, method_name)) or "")
            self.assertEqual(source.parent.name, "bot", method_name)
            self.assertEqual(source.parent.parent.name, "bbvg", method_name)

    def test_admin_action_is_queued_without_direct_state_mutation(self) -> None:
        panel = panel_runtime.TelegramPanelRuntime()
        with patch.object(admin_action_queue, "enqueue_remote", return_value="command-1") as enqueue:
            result = panel.dispatch_admin_action("confirm_finished_global", "wheel-1")
        enqueue.assert_called_once_with("confirm_finished_global", "wheel-1")
        self.assertTrue(result["queued"])
        self.assertFalse(result["state_changed"])

    def test_encrypted_state_and_retention(self) -> None:
        bot_private_state.self_test()
        privacy_retention.self_test()

    def test_monitor_health_and_incidents(self) -> None:
        monitor_health.self_test()
        incident_manager.self_test()
        system_checks_v2.self_test()

    def test_notification_preferences_and_personal_filters(self) -> None:
        notification_preferences_v2.self_test()
        notification_navigation.self_test()
        personal_reminder_filter.self_test()

    def test_source_and_wheel_contracts(self) -> None:
        panel_sources.self_test()
        source_registry.self_test()
        source_intelligence_alerts.self_test()
        wheel_lifecycle_v2.self_test()
        wheel_link_lifecycle.self_test()
        wheel_scenario_suite.self_test()
        wheel_metadata_quality.self_test()


if __name__ == "__main__":
    unittest.main()
