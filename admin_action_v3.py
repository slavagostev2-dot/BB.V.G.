from __future__ import annotations

import argparse
import json
import os
from typing import Any

import admin_action_v2


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
        raise ValueError("Колесо уже отсутствует в активном списке")
    sources = legacy.wheel_sources(state, normalized, context)
    stats_changed = legacy.monitor.data_store.record_admin_wheel_decision(
        stats,
        wheel_key=normalized,
        sources=sources,
        decision="confirmed",
        actor=actor or "admin",
    )

    removed = 0
    for name in (
        "active_wheels",
        "participating_wheels",
        "pending_posts",
        "button_contexts",
        "completed_wheel_alerts",
        "manual_deadlines",
    ):
        removed += legacy.remove_matching_records(state.setdefault(name, {}), normalized)

    state.setdefault("wheel_publications", {}).pop(normalized, None)
    state.setdefault("recently_completed_wheels", {})[normalized] = {
        "identifier": str(context.get("identifier") or normalized),
        "url": str(context.get("url") or ""),
        "sources": sources,
        "confirmed_finished_at": legacy.monitor.now_utc().isoformat(),
        "confirmed_finished_by": actor or "admin",
    }
    # Keep url_alerts/activation_alerts and seen records. They suppress the same
    # already finished Telegram publications without blocking a later event that
    # reuses the freestream identifier.
    return {
        "action": "confirm_finished_global",
        "value": value,
        "state_changed": True,
        "health_changed": False,
        "stats_changed": stats_changed,
        "detail": (
            f"Колесо завершено и удалено; рейтинг начислен источникам: "
            + (", ".join(f"@{source}" for source in sources) if sources else "источник не определён")
            + f". Очищено записей: {removed}"
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
    assert stats["sources"]["official"]["quality_score"] == 40
    assert stats["sources"]["collector"]["quality_score"] == 40
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
