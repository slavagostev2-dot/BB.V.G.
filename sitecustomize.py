from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import private_state


def _admin_recipients(config: dict[str, Any]) -> list[str]:
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    admin_ids = {
        str(value)
        for value in [config.get("owner_id"), *config.get("admins", [])]
        if str(value or "")
    }
    result = {
        str((users.get(user_id) or {}).get("chat_id") or user_id)
        for user_id in admin_ids
        if isinstance(users.get(user_id), dict)
    }
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if not result and fallback:
        result.add(fallback)
    return sorted(value for value in result if value)


try:
    import notification_router

    def _private_notification_config() -> tuple[dict[str, Any], bool]:
        return private_state.load_access({})

    notification_router.load_config = _private_notification_config
except Exception as exc:  # pragma: no cover
    print(f"WARNING private notification routing: {type(exc).__name__}: {exc}")


try:
    import source_tier_maintenance

    def _private_source_tier_recipients() -> list[str]:
        config, _ = private_state.load_access({})
        return _admin_recipients(config)

    source_tier_maintenance.notification_recipients = _private_source_tier_recipients
except Exception as exc:  # pragma: no cover
    print(f"WARNING private source tier routing: {type(exc).__name__}: {exc}")


_legacy_admin_action_self_test = (
    Path(sys.argv[0]).name == "admin_action.py" and "--self-test" in sys.argv
)

if not _legacy_admin_action_self_test:
    try:
        import monitor_data

        def _normalize_additive_rating(data: dict[str, Any]) -> bool:
            changed = False
            sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
            for entry in sources.values():
                if not isinstance(entry, dict):
                    continue
                decisions = entry.get("quality_decisions")
                if isinstance(decisions, dict):
                    for wheel_key, raw_points in list(decisions.items()):
                        points = max(0, int(raw_points or 0))
                        if points != int(raw_points or 0):
                            decisions[wheel_key] = points
                            changed = True
                    score = sum(max(0, int(value or 0)) for value in decisions.values())
                else:
                    score = max(0, int(entry.get("quality_score", 0) or 0))
                if int(entry.get("quality_score", 0) or 0) != score:
                    entry["quality_score"] = score
                    changed = True
            if data.get("source_rating_policy") != "additive_only_v1":
                data["source_rating_policy"] = "additive_only_v1"
                changed = True
            return changed

        _original_load_stats = monitor_data.load_stats
        _original_record_admin_wheel_decision = monitor_data.record_admin_wheel_decision

        def _load_stats_additive() -> dict[str, Any]:
            data = _original_load_stats()
            _normalize_additive_rating(data)
            return data

        def _record_admin_wheel_decision_additive(
            data: dict[str, Any],
            *,
            wheel_key: str,
            sources: list[str],
            decision: str,
            actor: str = "admin",
            at: Any = None,
        ) -> bool:
            normalized = str(wheel_key or "").strip().casefold()
            previous = data.get("admin_wheel_decisions", {}).get(normalized)
            previous_decision = (
                str(previous.get("decision") or "") if isinstance(previous, dict) else ""
            )
            if decision == "inactive" and previous_decision == "confirmed":
                return _normalize_additive_rating(data)
            changed = _original_record_admin_wheel_decision(
                data,
                wheel_key=wheel_key,
                sources=sources,
                decision=decision,
                actor=actor,
                at=at,
            )
            normalized_changed = _normalize_additive_rating(data)
            return changed or normalized_changed

        monitor_data.load_stats = _load_stats_additive
        monitor_data.record_admin_wheel_decision = _record_admin_wheel_decision_additive
    except Exception as exc:  # pragma: no cover
        print(f"WARNING additive source rating policy: {type(exc).__name__}: {exc}")
