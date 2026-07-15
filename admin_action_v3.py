from __future__ import annotations

import argparse
import json
import os
from typing import Any

import admin_action_v2
import wheel_lifecycle_v2


legacy = admin_action_v2.legacy
_original_apply_action = legacy.apply_action


def confirm_finished_global(
    state: dict[str, Any],
    stats: dict[str, Any],
    value: str,
) -> dict[str, Any]:
    key, actor = legacy.split_action_value(value)
    normalized = legacy.normalized_wheel_key(key)
    if not normalized:
        raise ValueError("Колесо не указано")
    context = legacy.wheel_context(state, normalized)
    if context is None:
        completed = state.get("recently_completed_wheels", {}).get(normalized)
        if isinstance(completed, dict):
            return {
                "action": "confirm_finished_global",
                "value": value,
                "state_changed": False,
                "health_changed": False,
                "stats_changed": False,
                "detail": "Колесо уже завершено",
            }
        raise ValueError("Колесо уже отсутствует в активном списке")
    sources = legacy.wheel_sources(state, normalized, context)
    entry = state.get("active_wheels", {}).get(normalized)
    event_entry = entry if isinstance(entry, dict) else context
    current = legacy.monitor.now_utc()
    removed = wheel_lifecycle_v2.complete_event(
        state,
        normalized,
        event_entry,
        current=current,
        reason="admin_finished",
    )
    completed = state.setdefault("recently_completed_wheels", {}).setdefault(
        normalized, {}
    )
    completed["sources"] = sources
    completed["confirmed_finished_at"] = current.isoformat()
    completed["confirmed_finished_by"] = actor or "admin"
    # Keep url_alerts/activation_alerts and seen records. They suppress the same
    # already finished Telegram publications without blocking a later event that
    # reuses the freestream identifier.
    return {
        "action": "confirm_finished_global",
        "value": value,
        "state_changed": True,
        "health_changed": False,
        "stats_changed": False,
        "detail": (
            "Колесо завершено и удалено без изменения рейтинга. "
            f"Очищено записей: {removed}"
        ),
    }


def apply_action_v3(
    state: dict[str, Any],
    health: dict[str, Any],
    stats: dict[str, Any],
    action: str,
    value: str,
) -> dict[str, Any]:
    if action == "confirm_finished_global":
        return confirm_finished_global(state, stats, value)
    result = _original_apply_action(state, health, stats, action, value)
    if action == "mark_inactive_global":
        key, _ = legacy.split_action_value(value)
        state.setdefault("wheel_publications", {}).pop(
            legacy.normalized_wheel_key(key), None
        )
        result["state_changed"] = True
    return result


legacy.apply_action = apply_action_v3


def self_test() -> None:
    state = {
        "active_wheels": {
            "wheel-a": {
                "identifier": "wheel-a",
                "url": "https://betboom.ru/freestream/wheel-a",
                "source": "official",
            }
        },
        "wheel_publications": {
            "wheel-a": [
                {"source": "official", "message_id": 1},
                {"source": "collector", "message_id": 2},
            ]
        },
    }
    health = {"sources": {}}
    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
    result = apply_action_v3(
        state, health, stats, "confirm_finished_global", "wheel-a|1"
    )
    assert result["state_changed"] is True
    assert "wheel-a" not in state["active_wheels"]
    assert "wheel-a" not in state["wheel_publications"]
    assert not stats.get("admin_wheel_decisions")
    second = apply_action_v3(
        state, health, stats, "confirm_finished_global", "wheel-a|1"
    )
    assert second["state_changed"] is False
    print("admin action v3 finished-wheel self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--action", default=os.getenv("ADMIN_ACTION", ""))
    parser.add_argument("--value", default=os.getenv("ADMIN_VALUE", ""))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.action or not args.value:
        raise SystemExit("ADMIN_ACTION and ADMIN_VALUE are required")
    result = legacy.run_action(args.action, args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
