from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import admin_action_queue
import admin_action_v3
import bbvg_monitor_main
import monitor_data
import notification_integrity_v2
import notification_preferences_v2
import notification_router
import rating_policy
import telegram_ui
import wheel_lifecycle_v2
import wheel_link_lifecycle
import wheel_publications_v2
import wheel_scenario_suite
from admin_panel_runtime_v38 import TelegramPanelRuntimeV38
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41, self_test as panel_self_test


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def stability_acceptance() -> None:
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
    assert bbvg_monitor_main.monitor._bbvg_restart_duplicate_guard_installed is True
    assert bbvg_monitor_main.monitor._bbvg_wheel_link_lifecycle_installed is True
    assert bbvg_monitor_main.monitor.UNKNOWN_DEDUP_HOURS == 2
    update_calls: list[str] = []
    original_telegram_api = bbvg_monitor_main.monitor.telegram_api
    try:
        bbvg_monitor_main.monitor.telegram_api = (
            lambda method, payload: update_calls.append(method) or {"ok": True, "result": []}
        )
        feedback = bbvg_monitor_main.monitor.process_bot_feedback({}, {}, {})
    finally:
        bbvg_monitor_main.monitor.telegram_api = original_telegram_api
    assert feedback == {"callbacks": 0, "participating": 0, "lists": 0}
    assert update_calls == []
    wheel_link_lifecycle.self_test()
    wheel_scenario_suite.self_test()

    system_checks = text("system_checks.py")
    assert "check_admin_panel_runtime(details, findings)" in system_checks
    assert '"bot_panel"' in system_checks

    panel_dispatch = inspect.getsource(TelegramPanelRuntimeV38.dispatch_admin_action)
    assert "enqueue_remote" in panel_dispatch
    assert "_apply_admin_action_direct" not in panel_dispatch
    remote_enqueue = inspect.getsource(admin_action_queue.enqueue_remote)
    assert '"sha"' in remote_enqueue
    assert "{409, 422}" in remote_enqueue

    queue, command_id = admin_action_queue.append_command(
        admin_action_queue.default_queue(),
        "confirm_finished_global",
        "wheel-test|99887766",
        command_id="production-acceptance",
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

    print("Stability acceptance passed")


def _rating_acceptance() -> None:
    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}

    def decide(wheel: str, sources: list[str], verdict: str) -> bool:
        return rating_policy.record_admin_wheel_decision(
            stats,
            wheel_key=wheel,
            sources=sources,
            decision=verdict,
            actor="admin",
            at=None,
            recorder=monitor_data.record_admin_wheel_decision,
        )

    assert decide("wheel-a", ["first", "second"], "confirmed") is True
    assert stats["sources"]["first"]["quality_score"] == 40
    assert stats["sources"]["second"]["quality_score"] == 40
    assert decide("wheel-a", ["first", "second"], "confirmed") is False
    assert decide("wheel-a", ["first", "second"], "inactive") is True
    assert stats["admin_wheel_decisions"]["wheel-a"]["decision"] == "inactive"
    for source in ("first", "second"):
        row = stats["sources"][source]
        assert row["quality_score"] == 0
        assert row["admin_rejected_wheels"] == 1
        assert "admin_confirmed_wheels" not in row
    assert decide("wheel-a", ["first", "second"], "inactive") is False


def _delivery_acceptance() -> None:
    original_path = notification_integrity_v2.STATE_PATH
    original_secret = os.environ.get("BOT_STATE_KEY")
    original_load = notification_router.load_config
    original_entries = dict(notification_integrity_v2._volatile_entries)
    try:
        with TemporaryDirectory() as temporary:
            notification_integrity_v2.STATE_PATH = Path(temporary) / "notification_delivery_state.json"
            notification_integrity_v2._volatile_entries.clear()
            os.environ["BOT_STATE_KEY"] = "production-acceptance-key"
            notification_integrity_v2.install(notification_router)
            notification_preferences_v2.install(notification_router)
            config = {
                "owner_id": "1",
                "admins": ["2", "4"],
                "blocked_users": ["4"],
                "settings": {"notifications": True},
                "users": {
                    "1": {"chat_id": "101", "notifications_enabled": True},
                    "2": {"chat_id": "202", "notifications_enabled": True},
                    "3": {
                        "chat_id": "303",
                        "notifications_enabled": True,
                        "notification_preferences": {"admin_system": True},
                    },
                    "4": {"chat_id": "404", "notifications_enabled": True},
                },
            }
            assert notification_router.recipients(config, True, "admin_system") == ["101", "202"]
            assert notification_router.recipients(config, True, "wheels") == ["101", "202", "303"]
            notification_router.load_config = lambda: (config, True)

            class FakeMonitor:
                sent: list[dict[str, Any]] = []

                @classmethod
                def telegram_api(cls, method: str, payload: dict[str, Any]) -> dict:
                    assert method == "sendMessage"
                    cls.sent.append(dict(payload))
                    return {"ok": True, "result": {"message_id": len(cls.sent)}}

            notification_router.install(FakeMonitor)
            first = FakeMonitor.send_message(
                "🎡 <b>Новое колесо BetBoom</b>\n"
                "Идентификатор: <code>wheel-a</code>\n📡 @first",
                url="https://betboom.ru/freestream/wheel-a",
            )
            second = FakeMonitor.send_message(
                "🎡 <b>Новое колесо BetBoom</b>\n"
                "Идентификатор: <code>wheel-a</code>\n📡 @second",
                url="https://betboom.ru/freestream/wheel-a?source=second",
            )
            assert first["result"]["sent"] == 3
            assert second["result"]["sent"] == 0
            assert second["result"]["hidden_skipped"] == 3
            assert len(FakeMonitor.sent) == 3
            raw = notification_integrity_v2.STATE_PATH.read_text(encoding="utf-8")
            for private_value in ("chat_id", "user_id", "wheel-a", "@first"):
                assert private_value not in raw
            entries = notification_integrity_v2.load_state()["entries"]
            assert len(entries) == 3
            assert all(notification_integrity_v2.HEX_DIGEST_RE.fullmatch(key) for key in entries)
    finally:
        notification_integrity_v2.STATE_PATH = original_path
        notification_router.load_config = original_load
        notification_integrity_v2._volatile_entries.clear()
        notification_integrity_v2._volatile_entries.update(original_entries)
        if original_secret is None:
            os.environ.pop("BOT_STATE_KEY", None)
        else:
            os.environ["BOT_STATE_KEY"] = original_secret


def _publication_acceptance() -> None:
    rows = [
        {
            "source": "first",
            "message_id": 10,
            "message_date": "2026-07-15T09:00:00+00:00",
            "message_url": "https://telegram.me/first/10",
        },
        {
            "source": "second",
            "message_id": 20,
            "message_date": "2026-07-15T09:01:00+00:00",
            "message_url": "https://telegram.me/second/20",
        },
    ]
    merged = wheel_publications_v2.merge_publications([], rows)
    assert {row["source"] for row in merged} == {"first", "second"}
    state = {
        "active_wheels": {},
        "inactive_wheels": {},
        "recently_completed_wheels": {
            "wheel-a": {"removed_at": "2026-07-15T09:02:00+00:00"}
        },
        "wheel_publications": {"wheel-a": merged},
    }
    assert wheel_publications_v2.prune_closed_publications(state) == 1
    assert state["wheel_publications"] == {}


def unified_logic_acceptance() -> None:
    _delivery_acceptance()
    _rating_acceptance()
    _publication_acceptance()
    print("Unified notification, source and administrator acceptance passed")


def ci_acceptance() -> None:
    workflow = text(".github/workflows/validate-current.yml")
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "contents: read" in workflow
    assert "continue-on-error" not in workflow
    assert "ref: main" not in workflow
    assert "git push" not in workflow
    assert "ci_verify_current_commit.py" in workflow
    assert "python -m pytest" in workflow
    assert "--cov-fail-under=80" in workflow
    assert "requirements-dev.txt" in workflow
    requirements = text("requirements-dev.txt")
    assert "pytest==" in requirements
    assert "pytest-cov==" in requirements
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    assert len(tests) >= 4
    combined = "\n".join(path.read_text(encoding="utf-8") for path in tests)
    for required in (
        "test_full_detection_to_telegram_and_two_source_deduplication",
        "test_simultaneous_delivery_claim_sends_once",
        "test_reused_freestream_identifier_selects_current_event",
        "test_registration_and_personal_action_merge_without_data_loss",
        "test_remote_queue_retries_conflict_with_same_command",
        "test_wrong_checkout_is_rejected",
    ):
        assert required in combined
    router = text("notification_router.py")
    integrity = text("notification_integrity_v2.py")
    assert "def claim_delivery" in router
    assert "release_delivery(dedup_key)" in router
    assert "def claim_delivery" in integrity
    assert "def complete_delivery" in integrity
    assert not (ROOT / "current_validation_state.json").exists()
    print("CI acceptance passed")


def interface_acceptance() -> None:
    telegram_ui.self_test()
    panel_self_test()
    workflow = text(".github/workflows/admin-bot.yml")
    assert "run: python admin_panel_runtime_v41.py" in workflow
    assert '"version": 41' in workflow
    assert "admin_panel_runtime_v41.py" in workflow
    assert "telegram_ui.py" in workflow
    user_callbacks = {
        str(button.get("callback_data") or "")
        for row in TelegramPanelRuntimeV41.compact_menu_rows(False)
        for button in row
    }
    admin_callbacks = {
        str(button.get("callback_data") or "")
        for row in TelegramPanelRuntimeV41.compact_menu_rows(True)
        for button in row
    }
    assert "page:status" in user_callbacks
    assert "page:control" not in user_callbacks
    assert "page:control" in admin_callbacks
    assert "page:status" not in admin_callbacks
    assert not telegram_ui.markup_issues(
        {"inline_keyboard": TelegramPanelRuntimeV41.compact_menu_rows(False)}
    )
    assert not telegram_ui.markup_issues(
        {"inline_keyboard": TelegramPanelRuntimeV41.compact_menu_rows(True)}
    )
    assert "Mini App — архивировано" in text("MINI_APP_ARCHIVED.md")
    assert (ROOT / "tests/test_ui_chapter4.py").exists()
    print("Interface acceptance passed")


def lifecycle_acceptance() -> None:
    transitions = set(wheel_lifecycle_v2.LIFECYCLE_TRANSITIONS)
    required = {
        ("detected", "future_availability", "scheduled_availability"),
        ("detected", "known_draw_time", "scheduled_draw"),
        ("detected", "unknown_draw_time", "active_unknown_time"),
        ("scheduled_availability", "availability_reached", "active_unknown_time"),
        ("active_unknown_time", "manual_time_set", "scheduled_draw"),
        ("scheduled_draw", "deadline_reached", "finished"),
        ("participating", "deadline_reached", "finished"),
        ("participating", "admin_inactive", "inactive"),
    }
    assert required <= transitions
    assert wheel_lifecycle_v2.FINAL_REMINDER_BEFORE_MINUTES == 5
    assert "rating_event_key" in inspect.getsource(admin_action_v3._original_apply_action)
    finished_source = inspect.getsource(admin_action_v3.confirm_finished_global)
    assert "record_admin_wheel_decision" in finished_source
    assert 'decision="confirmed"' in finished_source
    assert "rating_event_key" in finished_source
    assert (ROOT / "tests/test_chapter5_lifecycle.py").exists()
    assert "Mini App, Worker и D1 остаются архивированными" in text("CHAPTER_5_RU.md")
    print("Chapter 5 full wheel lifecycle acceptance passed")
    print("Completed-wheel source rating acceptance passed")


SECTIONS: dict[str, Callable[[], None]] = {
    "stability": stability_acceptance,
    "unified": unified_logic_acceptance,
    "ci": ci_acceptance,
    "interface": interface_acceptance,
    "lifecycle": lifecycle_acceptance,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="BB V.G. production acceptance suite")
    parser.add_argument(
        "--section",
        choices=("all", *SECTIONS),
        default="all",
        help="Run one acceptance section or the complete suite.",
    )
    args = parser.parse_args()
    selected = SECTIONS.values() if args.section == "all" else (SECTIONS[args.section],)
    for check in selected:
        check()
    print(f"Production acceptance passed: {args.section}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
