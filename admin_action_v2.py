from __future__ import annotations

import argparse
import html
import os
from datetime import timedelta
from typing import Any

import admin_action as legacy
import monitor
import notification_router_v2
import source_registry
import source_reputation


def _full_context(state: dict[str, Any], key: str) -> dict[str, Any]:
    normalized = legacy.normalized_wheel_key(key)
    context = legacy.wheel_context(state, normalized) or {}
    active = state.get("active_wheels", {}).get(normalized)
    if isinstance(active, dict):
        context = {**context, **active}
    context["wheel_key"] = normalized
    context.setdefault("identifier", normalized)
    return context


def _ensure_active_entry(
    state: dict[str, Any],
    context: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    key = legacy.normalized_wheel_key(str(context.get("wheel_key") or ""))
    if not key:
        raise ValueError("Колесо не указано")
    now = monitor.now_utc()
    active = state.setdefault("active_wheels", {})
    entry = active.get(key)
    if not isinstance(entry, dict):
        entry = {
            "identifier": str(context.get("identifier") or key),
            "url": str(context.get("url") or ""),
            "source": str(context.get("source") or "неизвестно"),
            "message_id": int(context.get("message_id", 0) or 0),
            "message_date": str(context.get("message_date") or now.isoformat()),
            "message_url": str(context.get("message_url") or ""),
            "message_text": str(context.get("message_text") or "")[:4000],
            "first_notified_at": now.isoformat(),
            "last_notification_at": now.isoformat(),
        }
        active[key] = entry
    entry["admin_verdict"] = "active"
    entry["admin_confirmed_at"] = now.isoformat()
    entry["admin_confirmed_by"] = str(actor or "admin")
    entry["status"] = "confirmed_by_admin"
    entry["last_checked_at"] = now.isoformat()
    deadline = monitor.parse_datetime(entry.get("deadline"))
    entry["expires_at"] = monitor.participation_expiry(
        deadline, current=now
    ).isoformat()
    return entry


def confirm_active(
    state: dict[str, Any],
    rating: dict[str, Any],
    key: str,
    actor: str,
) -> dict[str, Any]:
    context = _full_context(state, key)
    if not context.get("source") and not rating.get("wheels", {}).get(
        legacy.normalized_wheel_key(key)
    ):
        raise ValueError("Колесо не найдено")
    entry = _ensure_active_entry(state, context, actor)
    normalized = legacy.normalized_wheel_key(key)
    state.setdefault("admin_verdicts", {})[normalized] = {
        "status": "active",
        "decided_at": monitor.now_utc().isoformat(),
        "decided_by": str(actor or "admin"),
    }
    state.setdefault("inactive_wheels", {}).pop(normalized, None)
    legacy.remove_matching_records(state.setdefault("pending_posts", {}), normalized)

    result = source_reputation.apply_admin_verdict(
        rating,
        wheel_key=normalized,
        verdict="active",
        actor=actor,
        active_context=entry,
    )
    return result


def mark_inactive(
    state: dict[str, Any],
    rating: dict[str, Any],
    key: str,
    actor: str,
) -> dict[str, Any]:
    normalized = legacy.normalized_wheel_key(key)
    context = _full_context(state, normalized)
    rating_result = source_reputation.apply_admin_verdict(
        rating,
        wheel_key=normalized,
        verdict="inactive",
        actor=actor,
        active_context=context,
    )
    removed = legacy.mark_globally_inactive(state, normalized, actor)
    state.setdefault("admin_verdicts", {})[normalized] = {
        "status": "inactive",
        "decided_at": monitor.now_utc().isoformat(),
        "decided_by": str(actor or "admin"),
    }
    tombstone = state.setdefault("inactive_wheels", {}).setdefault(normalized, {})
    tombstone["source"] = str(context.get("source") or "")
    tombstone["url"] = str(context.get("url") or "")
    tombstone["rating_sources_updated"] = int(rating_result.get("sources", 0) or 0)
    return {**rating_result, "removed": removed}


def _notify_confirmed(entry: dict[str, Any]) -> None:
    if not os.getenv("BOT_TOKEN"):
        return
    notification_router_v2.install(monitor)
    identifier = str(entry.get("identifier") or entry.get("wheel_key") or "колесо")
    source = str(entry.get("source") or "неизвестно")
    url = str(entry.get("url") or "")
    deadline = monitor.parse_datetime(entry.get("deadline"))
    monitor.send_message(
        "✅ <b>Колесо BetBoom подтверждено администратором</b>\n\n"
        f"Источник: @{html.escape(source)}\n"
        f"Идентификатор: <code>{html.escape(identifier)}</code>\n"
        f"⏳ До прокрутки: <b>{html.escape(monitor.human_remaining(deadline))}</b>",
        url=url or None,
    )


def run_action(action: str, value: str) -> dict[str, Any]:
    state = legacy.load_json(legacy.STATE_PATH, {})
    health = legacy.load_json(legacy.HEALTH_PATH, {"version": 1, "sources": {}})
    rating = source_reputation.load()

    if action == "confirm_active":
        key, actor = legacy.split_action_value(value)
        result = confirm_active(state, rating, key, actor)
        changed = bool(result.get("changed"))
        detail = (
            f"Колесо подтверждено; рейтинг обновлён для "
            f"{int(result.get('sources', 0) or 0)} источников"
        )
        legacy.save_json(legacy.STATE_PATH, state)
        source_reputation.save(rating)
        source_registry.save_snapshot()
        if changed:
            entry = state.get("active_wheels", {}).get(
                legacy.normalized_wheel_key(key), {}
            )
            if isinstance(entry, dict):
                _notify_confirmed(entry)
        return {
            "action": action,
            "value": value,
            "state_changed": True,
            "health_changed": False,
            "rating_changed": changed,
            "detail": detail,
        }

    if action == "mark_inactive_global":
        key, actor = legacy.split_action_value(value)
        result = mark_inactive(state, rating, key, actor)
        legacy.save_json(legacy.STATE_PATH, state)
        source_reputation.save(rating)
        source_registry.save_snapshot()
        return {
            "action": action,
            "value": value,
            "state_changed": True,
            "health_changed": False,
            "rating_changed": bool(result.get("changed")),
            "detail": (
                f"Колесо признано неактивным; очищено записей: "
                f"{int(result.get('removed', 0) or 0)}; рейтинг обновлён для "
                f"{int(result.get('sources', 0) or 0)} источников"
            ),
        }

    result = legacy.apply_action(state, health, action, value)
    if result["state_changed"]:
        legacy.save_json(legacy.STATE_PATH, state)
    if result["health_changed"]:
        legacy.save_json(legacy.HEALTH_PATH, health)
    source_registry.save_snapshot()
    return result


def self_test() -> None:
    state = {
        "active_wheels": {
            "wheel1": {
                "identifier": "wheel1",
                "url": "https://betboom.ru/freestream/wheel1",
                "source": "alpha",
                "message_id": 10,
                "message_date": "2026-07-14T00:00:00+00:00",
                "message_url": "https://t.me/alpha/10",
            }
        },
        "pending_posts": {},
        "inactive_wheels": {},
    }
    rating = source_reputation.default_data()
    active = confirm_active(state, rating, "wheel1", "1")
    assert active["changed"]
    assert state["active_wheels"]["wheel1"]["admin_verdict"] == "active"
    inactive = mark_inactive(state, rating, "wheel1", "1")
    assert inactive["changed"]
    assert "wheel1" in state["inactive_wheels"]
    print("BB V.G. admin action v2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--action", default=os.getenv("ADMIN_ACTION", ""))
    parser.add_argument("--value", default=os.getenv("ADMIN_VALUE", ""))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.action:
        raise SystemExit("ADMIN_ACTION is required")
    result = run_action(args.action, args.value)
    print(result.get("detail") or result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
