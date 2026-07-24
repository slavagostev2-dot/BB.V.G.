from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def _empty_state() -> dict[str, Any]:
    return {
        "version": 6,
        "initialized_sources": [],
        "seen": {},
        "url_alerts": {},
        "activation_alerts": {},
        "pending_posts": {},
        "health": {},
        "button_contexts": {},
        "manual_overrides": {},
        "telegram_update_offset": 0,
        "active_wheels": {},
        "participating_wheels": {},
        "wheel_action_history": {},
        "wheel_generation_observations": {},
        "wheel_publications": {},
        "recently_completed_wheels": {},
        "inactive_wheels": {},
        "completed_wheel_alerts": {},
        "manual_deadlines": {},
        "auto_participation_events": {},
        "auto_participation_dispatch_events": {},
        "bot_commands_version": 0,
    }


def _composition_scenario() -> dict[str, Any]:
    import admin_action_queue
    import bbvg_monitor_main

    monitor = bbvg_monitor_main.monitor
    return {
        "bot_feedback_disabled": monitor.BOT_FEEDBACK_ENABLED is False,
        "admin_queue_installed": (
            monitor.process_admin_actions is admin_action_queue.process_pending
        ),
        "event_runtime": bool(monitor._bbvg_wheel_event_runtime_installed),
        "duplicate_guard": bool(monitor._bbvg_restart_duplicate_guard_installed),
        "link_lifecycle": bool(monitor._bbvg_wheel_link_lifecycle_installed),
        "wheel_lifecycle": bool(monitor._bbvg_wheel_lifecycle_v2_installed),
        "auto_dispatch": bool(monitor._bbvg_personal_reminder_filter_installed),
        "dispatcher_callable": callable(monitor.process_auto_participation_dispatch),
    }


def _pipeline_scenario() -> dict[str, Any]:
    import auto_participation_notifications
    import auto_participation_owner_sync
    import betboom_auto_participation
    import bbvg_monitor_main

    monitor = bbvg_monitor_main.monitor
    now = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    deadline = now + timedelta(minutes=30)
    server_start = now - timedelta(minutes=5)
    url = "https://betboom.ru/freestream/baseline-wheel"
    key = "baseline-wheel"
    message = monitor.Message(
        source="source_one",
        message_id=501,
        date=now - timedelta(minutes=1),
        text=f"Новое колесо {url}",
        message_url="https://telegram.me/source_one/501",
    )
    state = _empty_state()

    monitor.now_utc = lambda: now
    monitor.inspect_wheel_page = lambda _url: monitor.WheelInspection(
        status="active",
        deadline=deadline,
        method="baseline BetBoom API",
        action_id=1201,
        verification_status=monitor.WHEEL_VERIFICATION_CONFIRMED,
        server_start_at=server_start,
    )

    assessment = monitor.assess_new_wheel(message, url, state)
    assert assessment.should_notify is True
    assert assessment.status == "active"
    assert assessment.action_id == 1201
    assert assessment.server_start_at == server_start

    initial_messages: list[dict[str, Any]] = []

    def capture_initial(
        text: str,
        url: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        initial_messages.append(
            {"text": text, "url": url, "reply_markup": reply_markup}
        )
        return {"ok": True, "result": {"sent": 1, "message_id": 9001}}

    monitor.send_message = capture_initial
    monitor.notify_new_link(
        message,
        url,
        assessment.deadline,
        assessment.method,
        [],
        state,
        assessment.page_excerpt,
        action_id=assessment.action_id,
        available_at=assessment.available_at,
        verification_status=assessment.verification_status,
        server_start_at=assessment.server_start_at,
    )

    assert len(initial_messages) == 1
    assert "Новое колесо BetBoom" in initial_messages[0]["text"]
    entry = state["active_wheels"][key]
    assert entry["action_id"] == 1201
    assert monitor.parse_datetime(entry.get("server_start_at")) == server_start
    assert betboom_auto_participation._eligible_for_event_attempt(
        entry, monitor, now
    )

    participation = betboom_auto_participation.ParticipationResult(
        True,
        "participated",
        "BetBoom подтвердил участие",
    )
    betboom_auto_participation._mark_confirmed_participation(
        state,
        monitor,
        key,
        entry,
        participation,
        now,
    )
    assert state["active_wheels"][key]["participating"] is True

    base_token = auto_participation_owner_sync._event_token(entry, key)
    event_context = {
        field: entry[field]
        for field in (
            "identifier",
            "wheel_key",
            "url",
            "source",
            "message_id",
            "message_date",
            "message_url",
            "action_id",
            "server_start_at",
            "deadline",
        )
        if field in entry
    }
    state["auto_participation_events"] = {
        base_token: {
            "wheel_key": key,
            "account_key": auto_participation_notifications.PRIMARY_ACCOUNT_KEY,
            "account_label": auto_participation_notifications.PRIMARY_ACCOUNT_LABEL,
            "status": "participated",
            "attempted_at": now.isoformat(),
            "bot_success_pending_at": now.isoformat(),
            "event_context": event_context,
        },
        f"{base_token}#account:{auto_participation_notifications.SECONDARY_ACCOUNT_KEY}": {
            "wheel_key": key,
            "event_token": base_token,
            "account_key": auto_participation_notifications.SECONDARY_ACCOUNT_KEY,
            "account_label": auto_participation_notifications.SECONDARY_ACCOUNT_LABEL,
            "status": "participated",
            "attempted_at": now.isoformat(),
            "bot_success_pending_at": now.isoformat(),
            "event_context": event_context,
        },
    }

    groups = auto_participation_notifications._settled_event_groups(
        state,
        now=now,
    )
    assert list(groups) == [base_token]
    assert set(groups[base_token]) == {
        auto_participation_notifications.PRIMARY_ACCOUNT_KEY,
        auto_participation_notifications.SECONDARY_ACCOUNT_KEY,
    }

    text, markup = auto_participation_notifications._result_message(
        key,
        entry,
        groups[base_token],
    )
    assert "Участие принято" in text
    assert "Аккаунты: <b>1 и 2</b>" in text
    callbacks = {
        str(button.get("callback_data") or "")
        for row in markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict)
    }
    assert callbacks == {"bb:l:active", "page:menu"}

    return {
        "initial_notifications": len(initial_messages),
        "wheel_key": key,
        "event_token": base_token,
        "participating": bool(state["active_wheels"][key]["participating"]),
        "account_outcomes": sorted(groups[base_token]),
        "combined_result": "Участие принято" in text,
    }


def _invoke_scenario(name: str) -> dict[str, Any]:
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
        [sys.executable, str(Path(__file__).resolve()), "--scenario", name],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, completed.stderr
    return json.loads(lines[-1])


def test_current_production_composition_is_frozen() -> None:
    """The production install graph is checked without mutating the pytest process."""

    result = _invoke_scenario("composition")
    assert result and all(result.values())


def test_active_publication_reaches_one_combined_auto_participation_result() -> None:
    """Freeze discovery -> notification -> participation -> aggregation behavior."""

    result = _invoke_scenario("pipeline")
    assert result["initial_notifications"] == 1
    assert result["wheel_key"] == "baseline-wheel"
    assert result["participating"] is True
    assert result["combined_result"] is True
    assert result["account_outcomes"] == [
        "vyacheslav_primary",
        "vyacheslav_secondary",
    ]


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--scenario":
        raise SystemExit("usage: test_wheel_pipeline_baseline.py --scenario NAME")
    if sys.argv[2] == "composition":
        result = _composition_scenario()
    elif sys.argv[2] == "pipeline":
        result = _pipeline_scenario()
    else:
        raise SystemExit(f"unknown scenario: {sys.argv[2]}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
