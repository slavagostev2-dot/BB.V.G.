from pathlib import Path


def test_source_intelligence_entry_installs_wheel_only_retention() -> None:
    text = Path("source_intelligence_entry.py").read_text(encoding="utf-8")
    assert "source_intelligence_retention.install" in text
    assert "source_intelligence_alerts" in text
