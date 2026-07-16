from __future__ import annotations

import unittest

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_v2
import admin_action_v3
import admin_panel_runtime_v37
import admin_panel_runtime_v38
import admin_panel_runtime_v41
import bot_private_state
import incident_manager
import monitor_health
import notification_navigation
import notification_preferences_v2
import personal_reminder_filter
import privacy_retention
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
    def test_administrator_decisions(self) -> None:
        admin_action_v2.self_test()
        admin_action_v3.self_test()

    def test_runtime_chain_contracts_used_by_v41(self) -> None:
        panel_interface.self_test()
        panel_users.self_test()
        admin_panel_runtime_v37.self_test()
        admin_panel_runtime_v38.self_test()
        panel_runtime.self_test()
        admin_panel_runtime_v41.self_test()

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
