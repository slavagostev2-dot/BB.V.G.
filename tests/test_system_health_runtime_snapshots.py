from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "system-health.yml"


def test_system_health_loads_authoritative_runtime_snapshots_before_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Load authoritative runtime snapshots" in workflow
    assert "load_snapshot runtime-status" in workflow
    assert "load_snapshot runtime-state" in workflow
    for file_name in ("state.json", "source_stats.json", "source_health.json"):
        assert file_name in workflow

    load_position = workflow.index("Load authoritative runtime snapshots")
    checks_position = workflow.index("Run centralized bot checks")
    assert load_position < checks_position


def test_system_health_does_not_diagnose_from_stale_main_source_health() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    runtime_state_loop = (
        "for file in state.json source_stats.json source_health.json; do\n"
        "            load_snapshot runtime-state \"$file\""
    )
    assert runtime_state_loop in workflow
    assert "Authoritative runtime-status and runtime-state snapshots loaded" in workflow
