from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_monitor_uses_one_minute_fallback_and_separate_checkpoints() -> None:
    workflow = (ROOT / ".github/workflows/monitor.yml").read_text(encoding="utf-8")
    assert 'MONITOR_INTERVAL_MINUTES: "1"' in workflow
    assert 'os.getenv("MONITOR_INTERVAL_MINUTES", "1")' in workflow
    assert 'using {fallback} minute(s)' in workflow

    runtime_block = workflow.split("runtime_files=(", 1)[1].split(")", 1)[0]
    assert "state.json" in runtime_block
    assert "source_health.json" in runtime_block
    assert "monitor_status.json" not in runtime_block
    assert "notification_delivery_state.json" not in runtime_block
    assert "git restore --source=HEAD" in workflow
    assert "monitor_status.json notification_delivery_state.json" in workflow


def test_diagnostic_reports_configured_interval() -> None:
    source = (ROOT / "admin_panel_v2.py").read_text(encoding="utf-8")
    assert 'MONITOR_INTERVAL_MINUTES", "1"' in source
    assert '"monitor_interval_minutes": MONITOR_INTERVAL_MINUTES' in source
    assert 'settings.get(' in source
    assert '"monitor_interval_minutes",' in source
    assert 'interval_label = "минуту" if interval == 1' in source
    assert "основная проверка каждые 5 минут" not in source
