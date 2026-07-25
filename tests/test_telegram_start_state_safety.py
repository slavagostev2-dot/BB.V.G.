from __future__ import annotations

from pathlib import Path

from scripts.telegram_start_state_smoke import run_smoke


def test_telegram_start_and_state_smoke() -> None:
    run_smoke()


def test_release_validation_runs_telegram_start_state_smoke() -> None:
    validation = Path("scripts/validate_control_center.sh").read_text(encoding="utf-8")
    assert "python scripts/telegram_start_state_smoke.py" in validation


def test_snapshot_failures_cannot_be_replaced_with_empty_strings() -> None:
    source = Path("admin_panel_v2.py").read_text(encoding="utf-8")
    refresh = source.split("def refresh_snapshot", 1)[1].split("def snapshot", 1)[0]
    assert 'values[key] = ""' not in refresh
    assert "SnapshotUnavailableError" in refresh
    assert "return current" in refresh
