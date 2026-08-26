from __future__ import annotations

from pathlib import Path

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import notification_button_recovery
import source_intelligence
import source_intelligence_alerts

ROOT = Path(__file__).resolve().parents[1]
Panel = notification_button_recovery.TelegramPanelRuntimeButtonRecovery


def _callbacks(rows):
    return {
        str(button.get("callback_data") or "")
        for row in rows
        for button in row
        if isinstance(button, dict) and button.get("callback_data")
    }


def test_production_panel_uses_single_source_model() -> None:
    assert getattr(Panel, "_bbvg_single_source_model_installed", False) is True
    menu = _callbacks(Panel.compact_menu_rows(True))
    assert "page:active" in menu
    assert "page:sources" in menu
    assert "page:analytics" not in menu

    sources = _callbacks(Panel.source_menu_rows(True))
    assert "source_list:primary:0" in sources
    assert "source:add" in sources
    assert "page:intelligence" not in sources
    assert "page:discovery" not in sources
    assert not any("nightly" in value or "quiet" in value for value in sources)


def test_summary_notification_controls_are_removed_from_live_panel() -> None:
    keys = {item[0] for item in Panel._notification_options_for_role("owner")}
    assert not keys & {"daily_reports", "weekly_reports", "monthly_reports"}


def test_only_public_sources_contains_operational_inventory() -> None:
    active = [
        line.split("#", 1)[0].strip().lstrip("@")
        for line in (ROOT / "public_sources.txt").read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip().lstrip("@")
    ]
    assert "PIVOVAR_Cast" in active
    assert source_intelligence.ACTIVE_PATH == ROOT / "public_sources.txt"
    assert not hasattr(source_intelligence, "NIGHTLY_PATH")
    for obsolete in (
        "source_catalog.txt",
        "nightly_discovery.py",
        "nightly_discovery_entry.py",
        "source_tier_maintenance.py",
        "source_tier_maintenance_v2.py",
        ".github/workflows/nightly-discovery.yml",
        ".github/workflows/source-tier-maintenance.yml",
    ):
        assert not (ROOT / obsolete).exists()


def test_discovery_alert_has_one_add_path_and_24_hour_threshold() -> None:
    entry = {"source": "WheelSource", "public": True, "wheel_links_found": 1}
    _text, markup = source_intelligence_alerts.candidate_message("WheelSource", entry)
    callbacks = _callbacks(markup["inline_keyboard"])
    assert callbacks == {
        "intel:mode:fast:WheelSource",
        "intel:ignoreask:WheelSource",
    }
    assert source_intelligence_alerts.REMINDER_AFTER.total_seconds() == 24 * 60 * 60
