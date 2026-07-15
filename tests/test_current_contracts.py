from __future__ import annotations

import unittest

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_v2
import admin_action_v3
import admin_panel_runtime_v14
import admin_panel_runtime_v34
import admin_panel_runtime_v37
import admin_panel_runtime_v38
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
import wheel_metadata_quality


class CurrentProductionContractTests(unittest.TestCase):
    def test_administrator_decisions(self) -> None:
        admin_action_v2.self_test()
        admin_action_v3.self_test()

    def test_runtime_chain_contracts_used_by_v38(self) -> None:
        # v14 and v34 remain in the active inheritance chain until the later
        # cleanup chapter, so their assertions must not be silently skipped.
        admin_panel_runtime_v14.self_test()
        admin_panel_runtime_v34.self_test()
        admin_panel_runtime_v37.self_test()
        admin_panel_runtime_v38.self_test()

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
        source_registry.self_test()
        source_intelligence_alerts.self_test()
        wheel_lifecycle_v2.self_test()
        wheel_metadata_quality.self_test()


if __name__ == "__main__":
    unittest.main()
