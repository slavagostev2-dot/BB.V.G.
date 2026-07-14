from __future__ import annotations

import argparse
from typing import Any

import bot_notification_state
import notification_navigation
import notification_router
import system_checks as legacy

notification_router.load_config = bot_notification_state.load_config
legacy.notification_router.load_config = bot_notification_state.load_config
notification_navigation.install(legacy.monitor)


def check_miniapp_archived(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    del findings
    details["miniapp"] = {
        "status": "archived",
        "checked": False,
        "reason": "Mini App is frozen and excluded from the production bot runtime",
    }


def check_rating_consistency_additive(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    stats = legacy.load_json(legacy.SOURCE_STATS_PATH, {})
    state = legacy.load_json(legacy.RUNTIME_STATE_PATH, {})
    decisions = stats.get("admin_wheel_decisions") if isinstance(stats, dict) else {}
    decisions = decisions if isinstance(decisions, dict) else {}
    expected: dict[str, int] = {}
    inactive_decisions: list[str] = []
    for wheel, entry in decisions.items():
        if not isinstance(entry, dict):
            continue
        verdict = str(entry.get("decision") or "")
        points = 40 if verdict == "confirmed" else 0
        if verdict == "inactive":
            inactive_decisions.append(str(wheel).casefold())
        for source in entry.get("sources", []):
            key = str(source).casefold()
            if key:
                expected[key] = expected.get(key, 0) + points
    actual = {
        str(source).casefold(): max(0, int(entry.get("quality_score", 0) or 0))
        for source, entry in stats.get("sources", {}).items()
        if isinstance(entry, dict) and entry.get("quality_score") is not None
    }
    mismatches = sorted(
        key
        for key in set(expected) | set(actual)
        if expected.get(key, 0) != actual.get(key, 0)
    )
    active_keys = {str(key).casefold() for key in state.get("active_wheels", {})}
    participating_keys = {
        str(key).casefold() for key in state.get("participating_wheels", {})
    }
    inactive_leaks = sorted(set(inactive_decisions) & (active_keys | participating_keys))
    details["rating_consistency"] = {
        "policy": "additive_only_v1",
        "administrator_decisions": len(decisions),
        "rated_sources": len(expected),
        "score_mismatches": mismatches[:30],
        "inactive_wheel_leaks": inactive_leaks[:30],
    }
    if mismatches:
        findings.append(
            legacy.finding(
                "rating_score_mismatch",
                "Рейтинг источников не совпадает с решениями администратора",
                f"Несовпадения: {', '.join('@' + item for item in mismatches[:15])}.",
                severity="critical",
            )
        )
    if inactive_leaks:
        findings.append(
            legacy.finding(
                "inactive_wheel_leak",
                "Неактивное колесо осталось в пользовательских списках",
                f"Колёса: {', '.join(inactive_leaks[:15])}.",
                severity="critical",
            )
        )


legacy.check_miniapp_release = check_miniapp_archived
legacy.check_rating_consistency = check_rating_consistency_additive


def self_test() -> None:
    details: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    check_miniapp_archived(details, findings)
    assert details["miniapp"]["status"] == "archived"
    assert not findings
    assert legacy.monitor._bbvg_notification_navigation_installed is True
    print("BB V.G. bot-only system checks self-test passed")


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
