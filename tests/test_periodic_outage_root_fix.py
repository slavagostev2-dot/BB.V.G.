from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import incident_manager


UTC = timezone.utc


def test_control_plane_does_not_treat_heartbeat_commits_as_deployments() -> None:
    health = Path(".github/workflows/system-health.yml").read_text(encoding="utf-8")
    admin = Path(".github/workflows/admin-bot.yml").read_text(encoding="utf-8")
    push_block = health.split("  workflow_dispatch:", 1)[0]
    assert '"monitor_status.json"' not in push_block
    assert '"admin_panel_status.json"' not in push_block
    assert '"ai_runtime_state.json"' not in push_block
    assert "workflow_run:" not in health
    assert "cancel-in-progress: true" in health
    assert "Check Control Center heartbeat freshness" in health
    assert "Replace stale Control Center" in health
    assert "gh workflow run admin-bot.yml" in health
    assert 'cron: "37 * * * *"' not in admin


def test_volatile_incident_needs_confirmed_open_and_recovery() -> None:
    original_path = incident_manager.STATE_PATH
    original_now = incident_manager.now_utc
    current = [datetime(2026, 7, 25, 0, 0, tzinfo=UTC)]
    try:
        with TemporaryDirectory() as temporary:
            incident_manager.STATE_PATH = Path(temporary) / "incident_state.json"
            incident_manager.now_utc = lambda: current[0]  # type: ignore[assignment]
            finding = {
                "kind": "admin_panel_stale",
                "title": "Бот давно не принимает команды пользователей",
                "detail": "Первый кратковременный сигнал",
                "severity": "critical",
            }

            state = incident_manager.reconcile([finding], scope="test")
            assert incident_manager.pending_open(state) == []

            current[0] += timedelta(minutes=6)
            state = incident_manager.reconcile([finding], scope="test")
            opened = incident_manager.pending_open(state)
            assert len(opened) == 1
            key = str(opened[0]["key"])
            state = incident_manager.mark_notified([key], "open")

            current[0] += timedelta(minutes=1)
            state = incident_manager.reconcile([], scope="test")
            entry = state["incidents"][key]
            assert entry["status"] == "active"
            assert entry["recovery_confirmation_pending"] is True
            assert incident_manager.pending_resolved(state) == []

            current[0] += timedelta(minutes=11)
            state = incident_manager.reconcile([], scope="test")
            entry = state["incidents"][key]
            assert entry["status"] == "resolved"
            assert len(incident_manager.pending_resolved(state)) == 1
    finally:
        incident_manager.STATE_PATH = original_path
        incident_manager.now_utc = original_now  # type: ignore[assignment]


def test_current_panel_keeps_last_verified_snapshot() -> None:
    source = Path("admin_panel_v2.py").read_text(encoding="utf-8")
    refresh = source.split("def refresh_snapshot", 1)[1].split("def snapshot", 1)[0]
    assert 'values[key] = ""' not in refresh
    assert "return current" in refresh
    assert "SnapshotUnavailableError" in refresh
