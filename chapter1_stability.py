from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import admin_action_queue
import bbvg_monitor_main
import notification_integrity_v2
from admin_panel_runtime_v37 import TelegramPanelRuntimeV37


ROOT = Path(__file__).resolve().parent


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def self_test() -> None:
    for workflow in (
        ".github/workflows/monitor-66-live.yml",
        ".github/workflows/monitor-recovery-v32.yml",
    ):
        source = text(workflow)
        assert "schedule:" not in source
        assert "gh workflow run" not in source
        assert "if: ${{ false }}" in source

    health_workflow = text(".github/workflows/system-health.yml")
    assert "BB V.G. live 66-source monitor" not in health_workflow
    assert "BB V.G. monitor recovery v32" not in health_workflow

    monitor_workflow = text(".github/workflows/monitor.yml")
    assert "admin_action_queue.py" in monitor_workflow
    assert "BOT_STATE_KEY: ${{ secrets.BOT_STATE_KEY }}" in monitor_workflow
    assert 'BOT_FEEDBACK_ENABLED: "false"' in monitor_workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in monitor_workflow
    workflow_sources = [
        path.read_text(encoding="utf-8")
        for path in (ROOT / ".github/workflows").glob("*.yml")
    ]
    assert sum(source.count("python bbvg_monitor_main.py") for source in workflow_sources) == 1
    assert "state.json" not in text(".github/workflows/admin-action.yml")
    assert "monitor-66-live.yml" not in text(".github/workflows/activate-66-sources.yml")
    assert bbvg_monitor_main.monitor.BOT_FEEDBACK_ENABLED is False
    assert bbvg_monitor_main.monitor.process_admin_actions is admin_action_queue.process_pending
    assert "getUpdates" not in inspect.getsource(bbvg_monitor_main)

    system_checks = text("system_checks.py")
    assert "check_admin_panel_runtime(details, findings)" in system_checks
    assert '"bot_panel"' in system_checks

    panel_dispatch = inspect.getsource(TelegramPanelRuntimeV37.dispatch_admin_action)
    assert "enqueue_remote" in panel_dispatch
    assert "_apply_admin_action_direct" not in panel_dispatch
    remote_enqueue = inspect.getsource(admin_action_queue.enqueue_remote)
    assert '"sha"' in remote_enqueue
    assert "{409, 422}" in remote_enqueue

    queue, command_id = admin_action_queue.append_command(
        admin_action_queue.default_queue(),
        "confirm_finished_global",
        "wheel-test|99887766",
        command_id="chapter1-acceptance",
    )
    assert "99887766" not in json.dumps(queue)
    state: dict[str, Any] = {
        "active_wheels": {
            "wheel-test": {
                "identifier": "wheel-test",
                "source": "source-test",
                "url": "https://betboom.ru/freestream/wheel-test",
            }
        },
        "wheel_publications": {},
    }
    health: dict[str, Any] = {"sources": {}}
    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
    first = admin_action_queue.process_pending(state, health, stats, queue=queue)
    second = admin_action_queue.process_pending(state, health, stats, queue=queue)
    assert first["applied"] == 1
    assert second["applied"] == 0
    assert command_id in state["applied_admin_actions"]
    assert "wheel-test" not in state["active_wheels"]

    original_key = notification_integrity_v2.os.environ.pop("BOT_STATE_KEY", None)
    original_token = notification_integrity_v2.os.environ.get("BOT_TOKEN")
    try:
        notification_integrity_v2.os.environ["BOT_TOKEN"] = "token-is-not-state-key"
        try:
            notification_integrity_v2._secret()
        except notification_integrity_v2.NotificationIntegrityError:
            pass
        else:
            raise AssertionError("Persistent deduplication accepted BOT_TOKEN as a key")
    finally:
        if original_key is not None:
            notification_integrity_v2.os.environ["BOT_STATE_KEY"] = original_key
        if original_token is None:
            notification_integrity_v2.os.environ.pop("BOT_TOKEN", None)
        else:
            notification_integrity_v2.os.environ["BOT_TOKEN"] = original_token

    print("Chapter 1 stability acceptance tests passed")


if __name__ == "__main__":
    self_test()
