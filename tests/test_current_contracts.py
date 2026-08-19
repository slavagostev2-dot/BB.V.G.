from __future__ import annotations

import inspect
import threading
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
import notification_button_recovery
import notification_navigation
import notification_preferences_v2
import personal_reminder_filter
import privacy_retention
import source_intelligence
import source_intelligence_alerts
import source_registry
import system_checks_v2
import system_checks_v3
import telegram_post_links_v2
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
            root / ".github/workflows/telegram-source-transport.yml"
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
        self.assertIn(("nightly-discovery.yml", None), calls)
        self.assertIn(
            ("monitor.yml", {"continuous": "true", "replace": "true"}),
            calls,
        )
        self.assertIn(("telegram-source-transport.yml", None), calls)
        self.assertIn(("source-registry.yml", None), calls)

    def test_source_refresh_failure_does_not_hide_saved_source_change(self) -> None:
        bot = admin_runtime.RuntimeAdminBot()

        def dispatch(workflow: str, inputs: dict[str, str] | None = None) -> None:
            if workflow == "telegram-source-transport.yml":
                raise RuntimeError("temporary GitHub failure")

        bot.dispatch = dispatch  # type: ignore[method-assign]

        self.assertEqual(
            bot.refresh_source_runtime(),
            ["telegram-source-transport.yml"],
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

    def test_historical_panel_runtime_chain_is_absent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        remaining = [
            path.name
            for version in range(25, 41)
            if (path := root / f"admin_panel_runtime_v{version}.py").exists()
        ]
        self.assertEqual(remaining, [])

        active_contracts = (
            root / "preflight.py",
            root / "scripts/validate_control_center.sh",
            root / ".github/workflows/current-checks.yml",
            root / ".github/workflows/bot-recovery-smoke.yml",
            root / ".github/workflows/validate-private-state.yml",
            root / ".github/workflows/system-health.yml",
        )
        for path in active_contracts:
            text = path.read_text(encoding="utf-8")
            for version in range(25, 41):
                self.assertNotIn(f"admin_panel_runtime_v{version}", text, str(path))

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
        system_checks_v3.self_test()

    def test_notification_preferences_and_personal_filters(self) -> None:
        notification_preferences_v2.self_test()
        notification_navigation.self_test()
        personal_reminder_filter.self_test()

    def test_source_and_wheel_contracts(self) -> None:
        panel_sources.self_test()
        source_registry.self_test()
        source_intelligence_alerts.self_test()
        telegram_post_links_v2.self_test()
        wheel_lifecycle_v2.self_test()
        wheel_link_lifecycle.self_test()
        wheel_scenario_suite.self_test()
        wheel_metadata_quality.self_test()


if __name__ == "__main__":
    unittest.main()


def _source_block(path: str, start: str, end: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    return source.split(start, 1)[1].split(end, 1)[0]


def test_auto_participation_event_is_durable_before_notification_delivery() -> None:
    new_wheel = _source_block("monitor.py", "def notify_new_link(", "def notify_activation(")
    activation = _source_block("monitor.py", "def notify_activation(", "def fetch_all_sources(")
    availability = _source_block(
        "wheel_event_runtime.py",
        "def _availability_message(",
        "def process_due_availability(",
    )
    for block in (new_wheel, activation, availability):
        assert block.index("remember_active_wheel(") < block.index("send_message(")
        assert block.index("dispatch_notified_wheel_event") < block.index("send_message(")


def test_auto_participation_dispatch_uses_durable_outbox() -> None:
    source = Path("auto_participation_dispatch.py").read_text(encoding="utf-8")
    assert "claim_outbox" in source
    assert '{"auto_participation"}' in source
    assert "event_payload" in source
    assert "/contents/state.json" not in source
    assert "_push_state_before_dispatch" not in source


def test_auto_participation_outcomes_publish_to_control_center_runtime_state() -> None:
    workflow = Path(".github/workflows/auto-participation.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("--publish-runtime-state state.json") == 2
    assert "BBVG_RUNTIME_STATE_BRANCH: runtime-state" in workflow
    assert "Publish fast auto participation outcome [skip ci]" not in workflow
    assert "git fetch origin main runtime-state" in workflow
    assert "git show origin/runtime-state:state.json > state.json" in workflow
    assert workflow.index("git show origin/runtime-state:state.json > state.json") < workflow.index(
        "- name: Validate auto participation"
    )
    fast_publish = workflow.split("- name: Publish fast participation state", 1)[1]
    fast_publish = fast_publish.split(
        "- name: Recover fresh active wheels independently of monitor state", 1
    )[0]
    final_publish = workflow.split(
        "- name: Persist participation state without losing concurrent monitor updates",
        1,
    )[1]
    assert "git push origin HEAD:main" not in fast_publish
    assert "git push origin HEAD:main" not in final_publish

def test_auto_participation_outcome_always_creates_a_new_message() -> None:
    panel = panel_interface.PanelInterfaceRuntime.__new__(
        panel_interface.PanelInterfaceRuntime
    )
    panel.current_chat_id = "owner-chat"
    panel._edit_message_id = 777
    panel._force_new_message_context = threading.local()
    panel._remove_reply_keyboard_before_send = False
    calls: list[tuple[str, dict]] = []
    panel.telegram_api = lambda method, payload=None: (
        calls.append((method, dict(payload or {})))
        or {"ok": True, "result": {"message_id": 2000}}
    )
    panel.send = panel_interface.PanelInterfaceRuntime.send.__get__(
        panel,
        panel_interface.PanelInterfaceRuntime,
    )

    result = notification_button_recovery._run_with_outcome_delivery_claims(
        lambda current: (
            current.send("automatic outcome", chat_id="owner-chat")
            and {"completed": 1}
        ),
        panel,
    )

    assert result == {"completed": 1}
    assert [method for method, _payload in calls] == ["sendMessage"]
    assert panel._edit_message_id == 777
    assert not bool(getattr(panel._force_new_message_context, "active", False))

