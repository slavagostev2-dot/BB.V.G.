from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import incident_manager
import monitor_data
import source_transport_smoke
import telegram_transport


UTC = timezone.utc


def test_control_plane_does_not_treat_heartbeat_commits_as_deployments() -> None:
    health = Path(".github/workflows/system-health.yml").read_text(encoding="utf-8")
    admin = Path(".github/workflows/admin-bot.yml").read_text(encoding="utf-8")
    push_block = health.split("  workflow_dispatch:", 1)[0]
    assert '"monitor_status.json"' not in push_block
    assert '"admin_panel_status.json"' not in push_block
    assert '"ai_runtime_state.json"' not in push_block
    assert "workflow_run:" not in health
    assert "cancel-in-progress: true" in health
    assert "Check Control Center heartbeat freshness" in health
    assert "Replace stale Control Center" in health
    assert "gh workflow run admin-bot.yml" in health
    assert 'cron: "37 * * * *"' not in admin


def test_control_center_handoff_does_not_clone_full_runtime_history() -> None:
    admin = Path(".github/workflows/admin-bot.yml").read_text(encoding="utf-8")
    validation_checkout = admin.split("      - name: Checkout repository", 1)[1].split(
        "      - name: Resolve exact release SHA", 1
    )[0]
    live_checkout = admin.split(
        "      - name: Checkout validated repository", 1
    )[1].split("      - name: Set up Python", 1)[0]

    assert "fetch-depth: 1" in validation_checkout
    assert "fetch-depth: 1" in live_checkout
    assert "fetch-depth: 0" not in admin
    assert 'git fetch --no-tags --depth=1 origin "$release_sha"' in admin


def test_control_center_release_has_one_planned_replacement_owner() -> None:
    admin = Path(".github/workflows/admin-bot.yml").read_text(encoding="utf-8")
    current_validation = Path(".github/workflows/validate-current.yml").read_text(
        encoding="utf-8"
    )

    assert '      - "control_center_release.txt"' in admin
    assert "gh workflow run admin-bot.yml" not in current_validation
    assert "Start validated continuous monitor" in current_validation


def test_volatile_incident_needs_confirmed_open_and_recovery() -> None:
    original_path = incident_manager.STATE_PATH
    original_now = incident_manager.now_utc
    current = [datetime(2026, 7, 25, 0, 0, tzinfo=UTC)]
    try:
        with TemporaryDirectory() as temporary:
            incident_manager.STATE_PATH = Path(temporary) / "incident_state.json"
            incident_manager.now_utc = lambda: current[0]  # type: ignore[assignment]
            finding = {
                "kind": "admin_panel_stale",
                "title": "Бот давно не принимает команды пользователей",
                "detail": "Первый кратковременный сигнал",
                "severity": "critical",
            }

            state = incident_manager.reconcile([finding], scope="test")
            assert incident_manager.pending_open(state) == []

            current[0] += timedelta(minutes=6)
            state = incident_manager.reconcile([finding], scope="test")
            opened = incident_manager.pending_open(state)
            assert len(opened) == 1
            key = str(opened[0]["key"])
            state = incident_manager.mark_notified([key], "open")

            current[0] += timedelta(minutes=1)
            state = incident_manager.reconcile([], scope="test")
            entry = state["incidents"][key]
            assert entry["status"] == "active"
            assert entry["recovery_confirmation_pending"] is True
            assert incident_manager.pending_resolved(state) == []

            current[0] += timedelta(minutes=11)
            state = incident_manager.reconcile([], scope="test")
            entry = state["incidents"][key]
            assert entry["status"] == "resolved"
            assert len(incident_manager.pending_resolved(state)) == 1
    finally:
        incident_manager.STATE_PATH = original_path
        incident_manager.now_utc = original_now  # type: ignore[assignment]


def test_source_transport_incidents_also_need_confirmation() -> None:
    original_path = incident_manager.STATE_PATH
    original_now = incident_manager.now_utc
    current = [datetime(2026, 7, 31, 1, 0, tzinfo=UTC)]
    finding = {
        "kind": "source_timeout",
        "subject": "shadowkekw",
        "title": "Источник @shadowkekw не проверяется",
        "detail": "telegram.me не ответил за отведённое время",
        "severity": "critical",
    }
    try:
        with TemporaryDirectory() as temporary:
            incident_manager.STATE_PATH = Path(temporary) / "incident_state.json"
            incident_manager.now_utc = lambda: current[0]  # type: ignore[assignment]
            state = incident_manager.reconcile([finding], scope="test")
            assert incident_manager.pending_open(state) == []

            current[0] += timedelta(minutes=2)
            state = incident_manager.reconcile([], scope="test")
            assert incident_manager.pending_open(state) == []
            entry = next(iter(state["incidents"].values()))
            assert entry["status"] == "active"
            assert entry["recovery_confirmation_pending"] is True
    finally:
        incident_manager.STATE_PATH = original_path
        incident_manager.now_utc = original_now  # type: ignore[assignment]


def test_correlated_timeouts_retry_only_failed_subset_and_recover() -> None:
    sources = [f"source{index}" for index in range(10)]
    failed = set(sources[:8])
    calls: list[list[str]] = []

    def fetch(batch: list[str]):
        calls.append(list(batch))
        if len(calls) == 1:
            return (
                {source: [source] for source in batch if source not in failed},
                {source: "ReadTimeout: timed out" for source in batch if source in failed},
                [],
            )
        return ({source: [source] for source in batch}, {}, [])

    results, errors, empty = telegram_transport.fetch_with_transport_recovery(
        fetch,
        sources,
        attempts=2,
        sleep=lambda _: None,
    )
    assert calls == [sources, sources[:8]]
    assert set(results) == set(sources)
    assert errors == {}
    assert empty == []


def test_correlated_transient_quarantine_is_due_immediately() -> None:
    base = datetime(2026, 7, 31, 0, 47, tzinfo=UTC)
    health = {"sources": {}}
    for index in range(8):
        stamp = base + timedelta(seconds=index * 30)
        health["sources"][f"source{index}"] = {
            "status": "quarantined",
            "failure_code": "timeout",
            "quarantined_at": stamp.isoformat(),
            "next_recheck_at": (stamp + timedelta(hours=6)).isoformat(),
        }
    health["sources"]["isolated"] = {
        "status": "quarantined",
        "failure_code": "timeout",
        "quarantined_at": (base - timedelta(hours=2)).isoformat(),
        "next_recheck_at": (base + timedelta(hours=4)).isoformat(),
    }

    assert monitor_data.source_due_for_check(
        health, "source0", at=base + timedelta(minutes=5)
    )
    assert not monitor_data.source_due_for_check(
        health, "isolated", at=base + timedelta(minutes=5)
    )


def test_transport_snapshot_with_errors_is_not_labeled_success() -> None:
    assert source_transport_smoke.transport_status(170, 170, [], {}) == "success"
    assert (
        source_transport_smoke.transport_status(
            170, 170, [], {"aterionlegends": "ReadTimeout"}
        )
        == "degraded"
    )
    assert source_transport_smoke.transport_status(170, 169, ["missing"], {}) == "failure"


def test_current_panel_keeps_last_verified_snapshot() -> None:
    source = Path("admin_panel_v2.py").read_text(encoding="utf-8")
    refresh = source.split("def refresh_snapshot", 1)[1].split("def snapshot", 1)[0]
    assert 'values[key] = ""' not in refresh
    assert "return current" in refresh
    assert "SnapshotUnavailableError" in refresh


def test_stale_recovery_notification_cannot_surface_as_fresh_news() -> None:
    original_path = incident_manager.STATE_PATH
    original_now = incident_manager.now_utc
    current = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)
    try:
        with TemporaryDirectory() as temporary:
            incident_manager.STATE_PATH = Path(temporary) / "incident_state.json"
            incident_manager.now_utc = lambda: current  # type: ignore[assignment]
            old_key = incident_manager.incident_key("test", "admin_panel_stale")
            fresh_key = incident_manager.incident_key("test", "monitor_stale")
            incident_manager.STATE_PATH.write_text(
                __import__("json").dumps(
                    {
                        "version": 1,
                        "sequence": 2,
                        "incidents": {
                            old_key: {
                                "key": old_key,
                                "scope": "test",
                                "kind": "admin_panel_stale",
                                "status": "resolved",
                                "resolved_at": (current - timedelta(minutes=31)).isoformat(),
                                "resolved_sequence": 1,
                                "resolution_notification_pending": True,
                            },
                            fresh_key: {
                                "key": fresh_key,
                                "scope": "test",
                                "kind": "monitor_stale",
                                "status": "resolved",
                                "resolved_at": (current - timedelta(minutes=5)).isoformat(),
                                "resolved_sequence": 2,
                                "resolution_notification_pending": True,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            before = incident_manager.load_state()
            assert [row["key"] for row in incident_manager.pending_resolved(before)] == [fresh_key]

            after = incident_manager.reconcile([], scope="test")
            old = after["incidents"][old_key]
            fresh = after["incidents"][fresh_key]
            assert old["resolution_notification_pending"] is False
            assert old["resolution_notification_expired_at"] == current.isoformat()
            assert fresh["resolution_notification_pending"] is True
            assert [row["key"] for row in incident_manager.pending_resolved(after)] == [fresh_key]
    finally:
        incident_manager.STATE_PATH = original_path
        incident_manager.now_utc = original_now  # type: ignore[assignment]
