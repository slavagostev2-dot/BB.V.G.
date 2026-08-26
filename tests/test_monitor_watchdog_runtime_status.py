from pathlib import Path


def test_watchdog_reads_authoritative_runtime_status_branch() -> None:
    text = Path(".github/workflows/monitor-watchdog.yml").read_text(encoding="utf-8")
    assert "+refs/heads/runtime-status:refs/remotes/origin/runtime-status" in text
    assert "git show origin/runtime-status:monitor_status.json > monitor_status.json" in text
    assert "python monitor_health.py check" in text


def test_watchdog_checkout_is_shallow() -> None:
    text = Path(".github/workflows/monitor-watchdog.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 1" in text
    assert "fetch-depth: 0" not in text
