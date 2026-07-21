from __future__ import annotations

import argparse
from datetime import timedelta
from typing import Any

from bbvg import health_inspector as ai_health_inspector
import system_checks_v2 as current


legacy = current.legacy
_ORIGINAL_CHECK_INVENTORY = legacy.check_inventory
_ORIGINAL_CHECK_DISCOVERY_RUNTIME = legacy.check_discovery_runtime
_ORIGINAL_DELIVER_PENDING_NOTIFICATIONS = legacy.deliver_pending_notifications
DISCOVERY_INVENTORY_CONFIRMATION_HOURS = 6
SOURCE_REGISTRY_PATH = legacy.ROOT / "source_registry.json"


def check_inventory_allow_empty_nightly(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    """Treat an intentionally empty nightly tier as a valid primary-only policy."""

    before = len(findings)
    _ORIGINAL_CHECK_INVENTORY(details, findings)
    inventory = details.get("inventory") if isinstance(details, dict) else None
    inventory = inventory if isinstance(inventory, dict) else {}
    if int(inventory.get("nightly_configured", 0) or 0) != 0:
        return
    findings[before:] = [
        item
        for item in findings[before:]
        if item.get("kind") != "source_nightly_inventory"
    ]
    inventory["nightly_policy"] = "optional_empty"


def _source_registry_generated_at():
    registry = legacy.load_json(SOURCE_REGISTRY_PATH, {})
    if not isinstance(registry, dict):
        return None
    return legacy.parse_datetime(registry.get("generated_at"))


def check_discovery_runtime_with_sync_grace(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    before = len(findings)
    _ORIGINAL_CHECK_DISCOVERY_RUNTIME(details, findings)

    added = findings[before:]
    if not any(item.get("kind") == "discovery_inventory" for item in added):
        return

    discovery = details.get("discovery") if isinstance(details, dict) else None
    discovery = discovery if isinstance(discovery, dict) else {}
    last_run = legacy.parse_datetime(discovery.get("discovery_last_run_at"))
    registry_at = _source_registry_generated_at()
    if registry_at is None or (last_run is not None and registry_at <= last_run):
        return

    age = legacy.now_utc() - registry_at
    discovery["inventory_registry_generated_at"] = registry_at.isoformat()
    discovery["inventory_confirmation_window_hours"] = (
        DISCOVERY_INVENTORY_CONFIRMATION_HOURS
    )
    discovery["inventory_sync_age_minutes"] = max(
        0, int(age.total_seconds() // 60)
    )

    if age > timedelta(hours=DISCOVERY_INVENTORY_CONFIRMATION_HOURS):
        discovery["inventory_sync_state"] = "discovery_sync_overdue"
        return

    findings[before:] = [
        item for item in added if item.get("kind") != "discovery_inventory"
    ]
    discovery["inventory_sync_state"] = (
        "waiting_for_discovery_after_inventory_change"
    )


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


legacy.check_inventory = check_inventory_allow_empty_nightly
legacy.check_discovery_runtime = check_discovery_runtime_with_sync_grace
legacy.deliver_pending_notifications = deliver_pending_notifications_with_ai


def self_test() -> None:
    original_inventory = _ORIGINAL_CHECK_INVENTORY
    try:
        def empty_nightly(
            details: dict[str, Any], findings: list[dict[str, Any]]
        ) -> None:
            details["inventory"] = {
                "primary_configured": 168,
                "nightly_configured": 0,
            }
            findings.append(legacy.finding(
                "source_nightly_inventory",
                "Не задан ночной inventory источников",
                "Файл ночного наблюдения не содержит источников.",
            ))

        globals()["_ORIGINAL_CHECK_INVENTORY"] = empty_nightly
        inventory_details: dict[str, Any] = {}
        inventory_findings: list[dict[str, Any]] = []
        check_inventory_allow_empty_nightly(
            inventory_details, inventory_findings
        )
        assert not inventory_findings
        assert inventory_details["inventory"]["nightly_policy"] == "optional_empty"
    finally:
        globals()["_ORIGINAL_CHECK_INVENTORY"] = original_inventory

    original = _ORIGINAL_CHECK_DISCOVERY_RUNTIME
    original_now = legacy.now_utc
    original_registry_time = _source_registry_generated_at
    try:
        fixed_now = legacy.parse_datetime("2026-07-21T03:00:00+00:00")
        assert fixed_now is not None
        legacy.now_utc = lambda: fixed_now  # type: ignore[assignment]

        def mismatch_at(last_run_at: str):
            def mismatch(
                details: dict[str, Any], findings: list[dict[str, Any]]
            ) -> None:
                details["discovery"] = {
                    "discovery_last_run_at": last_run_at
                }
                findings.append(legacy.finding(
                    "discovery_inventory",
                    "Ночная проверка видит не весь утверждённый пул",
                    "В состоянии поиска записано 167, текущий inventory содержит 168.",
                ))

            return mismatch

        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = mismatch_at(
            "2026-07-21T00:00:00+00:00"
        )
        globals()["_source_registry_generated_at"] = lambda: legacy.parse_datetime(
            "2026-07-21T01:00:00+00:00"
        )
        details: dict[str, Any] = {}
        findings: list[dict[str, Any]] = []
        check_discovery_runtime_with_sync_grace(details, findings)
        assert not any(item.get("kind") == "discovery_inventory" for item in findings)
        assert details["discovery"]["inventory_sync_state"] == (
            "waiting_for_discovery_after_inventory_change"
        )

        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = mismatch_at(
            "2026-07-20T18:00:00+00:00"
        )
        globals()["_source_registry_generated_at"] = lambda: legacy.parse_datetime(
            "2026-07-20T19:00:00+00:00"
        )
        details = {}
        findings = []
        check_discovery_runtime_with_sync_grace(details, findings)
        assert any(item.get("kind") == "discovery_inventory" for item in findings)
        assert details["discovery"]["inventory_sync_state"] == (
            "discovery_sync_overdue"
        )

        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = mismatch_at(
            "2026-07-21T02:00:00+00:00"
        )
        globals()["_source_registry_generated_at"] = lambda: legacy.parse_datetime(
            "2026-07-21T01:00:00+00:00"
        )
        details = {}
        findings = []
        check_discovery_runtime_with_sync_grace(details, findings)
        assert any(item.get("kind") == "discovery_inventory" for item in findings)
        assert "inventory_sync_state" not in details["discovery"]
    finally:
        globals()["_ORIGINAL_CHECK_DISCOVERY_RUNTIME"] = original
        globals()["_source_registry_generated_at"] = original_registry_time
        legacy.now_utc = original_now  # type: ignore[assignment]

    assert legacy.check_inventory is check_inventory_allow_empty_nightly
    assert legacy.deliver_pending_notifications is deliver_pending_notifications_with_ai
    ai_health_inspector.self_test()
    current.self_test()
    print("BB V.G. primary-only inventory, discovery sync-grace and AI health inspector self-test passed")


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
