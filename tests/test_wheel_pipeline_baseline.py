from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _run(*args: str) -> str:
    env = dict(os.environ)
    env.update(
        {
            "BBVG_TEST_MODE": "1",
            "BOT_TOKEN": "test-bot-token",
            "BOT_STATE_KEY": "test-state-key",
            "BOT_CHAT_ID": "1",
            "ADMIN_USER_ID": "1",
            "TELEGRAM_WEB_DOMAIN": "telegram.me",
        }
    )
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def _ordered(text: str, markers: tuple[str, ...]) -> bool:
    positions = [text.index(marker) for marker in markers]
    return positions == sorted(positions)


def test_current_monitor_composition_order_is_documented_and_frozen() -> None:
    """Record the production install order without importing and mutating it."""

    source = _text("bbvg_monitor_main.py")
    assert _ordered(
        source,
        (
            "notification_preferences_v2.install(notification_router)",
            "recurring_wheel_events.install(monitor, runtime.base_runtime)",
            "telegram_transport.install(monitor)",
            "telegram_post_links_v2.install(monitor)",
            "wheel_event_runtime.install(monitor, runtime)",
            "wheel_publications_v2.install(monitor, runtime)",
            "restart_duplicate_guard.install(monitor)",
            "wheel_link_lifecycle.install(monitor)",
            "wheel_lifecycle_v2.install(monitor)",
            "personal_reminder_filter.install(monitor, notification_router)",
        ),
    )

    baseline = _text("engineering/WHEEL_PIPELINE_BASELINE_RU.md")
    for section in (
        "Текущий путь обнаружения",
        "Текущий путь первичного уведомления",
        "Текущий жизненный цикл",
        "Текущий путь автоучастия",
        "Текущий путь итогового сообщения",
        "Recovery-handoff",
        "Замороженные контракты этапа 1",
    ):
        assert section in baseline


def test_existing_scenario_contracts_cover_pipeline_boundaries() -> None:
    """Run the existing stable contracts for every boundary of the wheel path."""

    assert "10 scenarios" in _run("wheel_scenario_suite.py")
    assert "exact participation controls self-test passed" in _run(
        "betboom_participation_browser.py"
    )
    assert "authoritative-outcome self-test passed" in _run(
        "auto_participation_recovery.py", "--self-test"
    )
    assert "auto participation bot sync self-test passed" in _run(
        "auto_participation_bot_sync.py", "--self-test"
    )
    assert "unified auto participation notifications self-test passed" in _run(
        "auto_participation_notifications.py"
    )


def test_auto_participation_workflow_order_is_frozen() -> None:
    """Keep the current multi-stage worker order visible before its later cleanup."""

    workflow = _text(".github/workflows/auto-participation.yml")
    assert _ordered(
        workflow,
        (
            "- name: Run event-based auto participation",
            "- name: Retry current active wheels immediately",
            "- name: Run second BetBoom account on fast result",
            "- name: Run xFLARXx BetBoom account on fast result",
            "- name: Queue fast outcomes for Control Center",
            "- name: Recover fresh active wheels independently of monitor state",
            "- name: Run second BetBoom account after full recovery",
            "- name: Run xFLARXx BetBoom account after full recovery",
            "- name: Queue confirmed participation for Control Center",
            "- name: Persist participation state without losing concurrent monitor updates",
        ),
    )
