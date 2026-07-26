from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import admin_bot
import betboom_account_participation
import monitor
from bbvg.bot.foundation import PanelFoundationMixin
from bbvg.bot.interface import PanelInterfaceRuntime


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


def test_control_center_reads_live_runtime_branches(monkeypatch) -> None:
    panel = object.__new__(admin_bot.AdminBot)
    calls: list[tuple[str, str | None]] = []

    def get_file(path: str, *, branch: str | None = None) -> tuple[str, str]:
        calls.append((path, branch))
        return json.dumps({"active_wheels": {}}), "blob"

    panel.get_file = get_file  # type: ignore[method-assign]
    assert panel.get_json_file("state.json", {"bad": True}) == {
        "active_wheels": {}
    }
    assert calls == [("state.json", "runtime-state")]

    interface = object.__new__(PanelInterfaceRuntime)
    interface.get_file = (  # type: ignore[method-assign]
        lambda path, *, branch=None: (
            json.dumps({"last_successful_iteration_at": "2026-07-26T04:00:00+00:00"}),
            "blob",
        )
    )
    status = interface._monitor_status()
    assert status["last_successful_iteration_at"].startswith("2026-07-26")
    assert interface._last_verified_monitor_status == status

    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary GitHub failure")

    interface.get_file = fail  # type: ignore[method-assign]
    assert interface._monitor_status() == status
    assert interface._monitor_status_from_cache is True


def test_intelligence_state_keeps_nonzero_snapshot_on_github_failure() -> None:
    panel = object.__new__(PanelFoundationMixin)
    expected = {
        "version": 1,
        "last_run_summary": {
            "sources_scanned": 170,
            "references_found": 312,
        },
        "candidates": {},
        "edges": {},
        "runs": [],
    }
    panel.get_file = lambda path: (json.dumps(expected), "blob")  # type: ignore[method-assign]
    assert panel.intelligence_state()["last_run_summary"]["sources_scanned"] == 170

    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary GitHub failure")

    panel.get_file = fail  # type: ignore[method-assign]
    recovered = panel.intelligence_state()
    assert recovered["last_run_summary"]["sources_scanned"] == 170
    assert recovered["last_run_summary"]["references_found"] == 312


def test_stale_monitor_heartbeat_is_not_described_as_stopped_scan() -> None:
    panel = object.__new__(PanelInterfaceRuntime)
    sent: list[str] = []
    old = (datetime.now(UTC) - timedelta(minutes=37)).isoformat()
    panel.snapshot = lambda force=False: type(  # type: ignore[method-assign]
        "Snapshot",
        (),
        {"fast": ["one"], "nightly": [], "state": {}},
    )()
    panel._monitor_status = lambda: {  # type: ignore[method-assign]
        "last_successful_iteration_at": old,
        "checked_sources": 169,
        "reachable_sources": 169,
        "source_errors": 0,
        "iteration": 83,
        "last_iteration_duration_seconds": 29,
    }
    panel.load_source_registry = lambda: {"summary": {"total": 169}}  # type: ignore[method-assign]
    panel.workflow_run = lambda workflow: {"status": "in_progress"}  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: []  # type: ignore[method-assign]
    panel.is_admin = lambda: False  # type: ignore[method-assign]
    panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: sent.append(text)  # type: ignore[method-assign]

    panel.show_status()

    text = sent[-1]
    assert "Monitor запущен; публикация свежей телеметрии задерживается" in text
    assert "Последний подтверждённый обход" in text
    assert "Номер подтверждённого цикла: <b>83</b>" in text
    assert "Длительность полного обхода: <b>29 сек.</b>" in text
    assert "Последняя проверка каналов" not in text


def test_active_button_miss_is_retryable_until_page_closes() -> None:
    assert "button_not_found" in betboom_account_participation.TRANSIENT_STATUSES
    assert (
        "button_not_found"
        not in betboom_account_participation.TERMINAL_FAILURE_STATUSES
    )
    current = datetime(2026, 7, 26, 4, 0, tzinfo=UTC)
    assert betboom_account_participation._should_attempt(
        {
            "status": "button_not_found",
            "retry_after_at": (current - timedelta(seconds=1)).isoformat(),
        },
        current,
    )
    assert not betboom_account_participation._should_attempt(
        {
            "status": "button_not_found",
            "retry_after_at": (current + timedelta(seconds=1)).isoformat(),
        },
        current,
    )


def test_early_close_settles_every_account_and_preserves_success(monkeypatch) -> None:
    current = datetime(2026, 7, 26, 4, 0, tzinfo=UTC)
    start = current - timedelta(minutes=2)
    deadline = current + timedelta(minutes=20)
    key = "earlywheel"
    base = f"{key}#action:9001:{start.isoformat()}"
    state = {
        "active_wheels": {
            key: {
                "wheel_key": key,
                "identifier": key,
                "url": f"https://betboom.ru/freestream/{key}",
                "action_id": 9001,
                "server_start_at": start.isoformat(),
                "deadline": deadline.isoformat(),
                "expires_at": (deadline + timedelta(minutes=30)).isoformat(),
            }
        },
        "participating_wheels": {},
        "recently_completed_wheels": {},
        "auto_participation_account_registry": {
            "vyacheslav_primary": {
                "account_key": "vyacheslav_primary",
                "account_label": "Account 1",
                "account_owner": "vyacheslav",
                "account_order": 10,
                "enabled": True,
            },
            "vyacheslav_secondary": {
                "account_key": "vyacheslav_secondary",
                "account_label": "Account 2",
                "account_owner": "vyacheslav",
                "account_order": 20,
                "enabled": True,
            },
            "xflarxx_primary": {
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "account_owner": "xflarxx",
                "account_order": 10,
                "enabled": True,
            },
        },
        "auto_participation_events": {
            base: {
                "wheel_key": key,
                "event_token": base,
                "account_key": "vyacheslav_primary",
                "status": "participated",
            },
            f"{base}#account:vyacheslav_secondary": {
                "wheel_key": key,
                "event_token": base,
                "account_key": "vyacheslav_secondary",
                "status": "button_not_found",
            },
        },
    }
    settled = monitor._finalize_closed_account_outcomes(
        state,
        key,
        state["active_wheels"][key],
        current=current,
        closed_early=True,
    )

    assert settled == 2
    assert (
        state["auto_participation_events"][base]["status"] == "participated"
    )
    for account in ("vyacheslav_secondary", "xflarxx_primary"):
        token = f"{base}#account:{account}"
        record = state["auto_participation_events"][token]
        assert record["status"] == "participation_closed"
        assert record["bot_failure_pending_at"] == current.isoformat()

    source = (ROOT / "monitor.py").read_text(encoding="utf-8")
    assert '"wheel_closed"' in source
    assert "_finalize_closed_account_outcomes(" in source


def test_monitor_runtime_state_isolated_and_active_interval_capped() -> None:
    workflow = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(
        encoding="utf-8"
    )
    assert "refs/heads/runtime-state" in workflow
    assert "-f branch=runtime-state" in workflow
    assert 'git push origin "HEAD:${GITHUB_REF_NAME:-main}"' not in workflow
    assert "value = min(value, 3)" in workflow

