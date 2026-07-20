from __future__ import annotations

import argparse
from datetime import timedelta
from typing import Any

from bbvg import health_inspector as ai_health_inspector
import system_checks_v2 as current

legacy = current.legacy
_ORIGINAL_CHECK_DISCOVERY_RUNTIME = legacy.check_discovery_runtime
_ORIGINAL_DELIVER_PENDING_NOTIFICATIONS = legacy.deliver_pending_notifications
DISCOVERY_INVENTORY_CONFIRMATION_HOURS = 6


def check_discovery_runtime_with_sync_grace(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    before = len(findings)
    _ORIGINAL_CHECK_DISCOVERY_RUNTIME(details, findings)

    discovery = details.get("discovery") if isinstance(details, dict) else None
    discovery = discovery if isinstance(discovery, dict) else {}
    last_run = legacy.parse_datetime(discovery.get("discovery_last_run_at"))
    if last_run is None:
        return

    age = legacy.now_utc() - last_run
    if age <= timedelta(hours=DISCOVERY_INVENTORY_CONFIRMATION_HOURS):
        return

    added = findings[before:]
    if not any(item.get("kind") == "discovery_inventory" for item in added):
        return

    findings[before:] = [
        item for item in added if item.get("kind") != "discovery_inventory"
    ]
    discovery["inventory_sync_state"] = "waiting_for_next_discovery_run"
    discovery["inventory_snapshot_age_hours"] = max(0, int(age.total_seconds() // 3600))
    discovery["inventory_confirmation_window_hours"] = DISCOVERY_INVENTORY_CONFIRMATION_HOURS


def deliver_pending_notifications_with_ai(
    state: dict[str, Any], details: dict[str, Any]
) -> None:
    incidents = state.get("incidents") if isinstance(state.get("incidents"), dict) else {}
    active_findings = [
        entry
        for entry in incidents.values()
        if isinstance(entry, dict)
        and entry.get("status") == "active"
        and entry.get("scope") == legacy.SCOPE
    ]
    insight = ai_health_inspector.inspect(details, active_findings)
    details["ai_health_inspector"] = insight

    opened = legacy.incident_manager.pending_open(state)
    resolved = legacy.incident_manager.pending_resolved(state)
    delivery = {
        "opened": len(opened),
        "resolved": len(resolved),
        "digest_sent": False,
        "messages_attempted": 1 if opened or resolved else 0,
        "health_inspector_mode": insight.get("mode"),
        "health_inspector_status": insight.get("ai_status"),
    }
    if opened or resolved:
        message = legacy.incident_manager.format_digest_message(opened, resolved)
        note = ai_health_inspector.admin_note(insight) if opened else ""
        if note:
            message = f"{message}\n\n{note}"[:4000]
        try:
            legacy.monitor.send_message(message)
        except Exception as exc:
            delivery["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        else:
            if opened:
                legacy.incident_manager.mark_notified(
                    [str(entry.get("key")) for entry in opened], "open"
                )
            if resolved:
                legacy.incident_manager.mark_notified(
                    [str(entry.get("key")) for entry in resolved], "resolved"
                )
            delivery["digest_sent"] = True
    details["incident_delivery"] = delivery


legacy.check_discovery_runtime = check_discovery_runtime_with_sync_grace
legacy.deliver_pending_notifications = deliver_pending_notifications_with_ai


def self_test() -> None:
    original = _ORIGINAL_CHECK_DISCOVERY_RUNTIME
    original_now = legacy.now_utc
    try:
        fixed_now = legacy.parse_datetime("2026-07-19T15:00:00+00:00")
        assert fixed_now is not None
        legacy.now_utc = lambda: fixed_now  # type: ignore[assignment]

        def stale_mismatch(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
            details["discovery"] = {"discovery_last_run_at": "2026-07-19T05:00:00+00:00"}
            findings.append(legacy.finding(
                "discovery_inventory",
                "Ночная проверка видит не весь утверждённый пул",
                "В состоянии поиска записано 162, текущий inventory содержит 164.",
            ))

        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = stale_mismatch
        details: dict[str, Any] = {}
        findings: list[dict[str, Any]] = []
        check_discovery_runtime_with_sync_grace(details, findings)
        assert not any(item.get("kind") == "discovery_inventory" for item in findings)
        assert details["discovery"]["inventory_sync_state"] == "waiting_for_next_discovery_run"

        def fresh_mismatch(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
            details["discovery"] = {"discovery_last_run_at": "2026-07-19T14:00:00+00:00"}
            findings.append(legacy.finding(
                "discovery_inventory",
                "Ночная проверка видит не весь утверждённый пул",
                "В состоянии поиска записано 162, текущий inventory содержит 164.",
            ))

        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = fresh_mismatch
        details = {}
        findings = []
        check_discovery_runtime_with_sync_grace(details, findings)
        assert any(item.get("kind") == "discovery_inventory" for item in findings)
    finally:
        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = original
        legacy.now_utc = original_now  # type: ignore[assignment]

    assert legacy.deliver_pending_notifications is deliver_pending_notifications_with_ai
    ai_health_inspector.self_test()
    current.self_test()
    print("BB V.G. discovery sync-grace and AI health inspector self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
