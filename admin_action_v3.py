from __future__ import annotations

import argparse
import json
import os
from typing import Any

import admin_action_v2
import personal_wheel_voting
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
                "detail": "Колесо уже завершено; рейтинг уже был начислен ранее",
            }
        raise ValueError("Колесо уже отсутствует в активном списке")

    sources = legacy.wheel_sources(state, normalized, context)
    entry = state.get("active_wheels", {}).get(normalized)
    event_entry = entry if isinstance(entry, dict) else context
    current = legacy.monitor.now_utc()
    rating_key = wheel_lifecycle_v2.rating_event_key(normalized, event_entry)
    rating_changed = legacy.monitor.data_store.record_admin_wheel_decision(
        stats,
        wheel_key=rating_key,
        sources=sources,
        decision="confirmed",
        actor="admin",
        at=current,
    )

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
    completed["confirmed_finished_by"] = "admin"
    completed["rating_event_key"] = rating_key
    detail = (
        "Колесо завершено; рейтинг источников начислен: по 40 очков каждому. "
        if rating_changed
        else "Колесо завершено; рейтинг уже был начислен ранее. "
    )
    return {
        "action": "confirm_finished_global",
        "value": value,
        "state_changed": True,
        "health_changed": False,
        "stats_changed": rating_changed,
        "detail": detail + f"Очищено записей: {removed}",
    }


def _canonical_personal_vote_event_key(
    state: dict[str, Any],
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> str:
    """Resolve all participation buttons for one live API event to one key."""

    wheel_key = str(payload.get("wheel_key") or "").casefold()
    active = state.get("active_wheels", {}).get(wheel_key)
    for event_entry in (active, context):
        if not isinstance(event_entry, dict):
            continue
        # Only override the button-supplied event key when runtime has a strong
        # API identity. This keeps legacy/event-only records backwards-compatible.
        if event_entry.get("generation_id") or event_entry.get("action_id"):
            canonical = personal_wheel_voting.wheel_event_key(wheel_key, event_entry)
            if canonical:
                return canonical
    return str(payload.get("event_key") or wheel_key).casefold()


def record_personal_vote_action(
    state: dict[str, Any],
    stats: dict[str, Any],
    value: str,
) -> dict[str, Any]:
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON личного голоса") from exc
    payload = personal_wheel_voting.normalize_vote_payload(raw)
    context = legacy.wheel_context(state, payload["wheel_key"])
    event_key = _canonical_personal_vote_event_key(state, payload, context)
    sources = list(payload["sources"])
    if context is not None:
        for source in legacy.wheel_sources(state, payload["wheel_key"], context):
            if source and source.casefold() not in {item.casefold() for item in sources}:
                sources.append(source)
    changed = personal_wheel_voting.record_personal_vote(
        stats,
        event_key=event_key,
        sources=sources,
        actor=payload["actor"],
        role=payload["role"],
        weight=payload["weight"],
        at=legacy.monitor.now_utc(),
    )
    return {
        "action": "record_personal_vote",
        "value": event_key,
        "state_changed": False,
        "health_changed": False,
        "stats_changed": changed,
        "detail": (
            f"Личный голос учтён для {len(sources)} источников"
            if changed
            else "Личный голос уже был учтён ранее"
        ),
    }


def apply_action_v3(
    state: dict[str, Any],
    health: dict[str, Any],
    stats: dict[str, Any],
    action: str,
    value: str,
) -> dict[str, Any]:
    if action == "record_personal_vote":
        return record_personal_vote_action(state, stats, value)
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
                "message_date": "2026-07-16T10:00:00+00:00",
                "action_id": 10,
                "event_id": "event-a",
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
    actor = personal_wheel_voting.actor_vote_token("42", secret="test-secret")
    first_payload = {
        "wheel_key": "wheel-a",
        "event_key": "wheel-a",
        "actor": actor,
        "role": "user",
        "weight": 1,
        "sources": ["official"],
    }
    second_payload = {
        **first_payload,
        "event_key": "wheel-a#action:10",
    }
    first_vote = apply_action_v3(
        state, health, stats, "record_personal_vote", json.dumps(first_payload)
    )
    second_vote = apply_action_v3(
        state, health, stats, "record_personal_vote", json.dumps(second_payload)
    )
    assert first_vote["stats_changed"] is True
    assert second_vote["stats_changed"] is False
    assert first_vote["value"] == second_vote["value"] == "wheel-a#action:10"
    assert stats["sources"]["official"]["quality_score"] == 1
    assert stats["sources"]["collector"]["quality_score"] == 1

    legacy_stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
    result = apply_action_v3(
        state, health, legacy_stats, "confirm_finished_global", "wheel-a|1"
    )
    assert result["state_changed"] is True
    assert result["stats_changed"] is True
    assert "wheel-a" not in state["active_wheels"]
    assert "wheel-a" not in state["wheel_publications"]
    assert legacy_stats["sources"]["official"]["quality_score"] == 40
    assert legacy_stats["sources"]["collector"]["quality_score"] == 40
    decisions = legacy_stats.get("admin_wheel_decisions", {})
    assert len(decisions) == 1
    assert next(iter(decisions.values()))["actor"] == "admin"
    assert state["recently_completed_wheels"]["wheel-a"]["confirmed_finished_by"] == "admin"
    print("admin action v3 personal vote idempotency self-test passed")


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
