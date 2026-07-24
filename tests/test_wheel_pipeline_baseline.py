from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _ordered(text: str, markers: tuple[str, ...]) -> bool:
    positions = [text.index(marker) for marker in markers]
    return positions == sorted(positions)


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


def test_wheel_scenario_suite_is_part_of_the_baseline() -> None:
    """Run the existing cross-module wheel scenario suite in isolation."""

    assert "10 scenarios" in _run("wheel_scenario_suite.py")
