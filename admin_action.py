from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import monitor
import wheel_lifecycle_v2


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
HEALTH_PATH = ROOT / "source_health.json"
STATS_PATH = ROOT / "source_stats.json"


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
    return value if isinstance(value, dict) else default


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def normalized_wheel_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return monitor.wheel_key(raw) if "://" in raw else raw.casefold()
    except Exception:
        return raw.casefold()


def record_wheel_key(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    value = str(record.get("wheel_key") or record.get("identifier") or "")
    if value:
        return normalized_wheel_key(value)
    return normalized_wheel_key(str(record.get("url") or ""))


def wheel_context(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    normalized = normalized_wheel_key(key)
    entry = state.get("active_wheels", {}).get(normalized)
    if not isinstance(entry, dict):
        for candidate_key, candidate in state.get("active_wheels", {}).items():
            if str(candidate_key).casefold() == normalized and isinstance(candidate, dict):
                entry = candidate
                normalized = str(candidate_key)
                break
    if isinstance(entry, dict):
        return {
            "wheel_key": normalized,
            "identifier": str(entry.get("identifier") or normalized),
            "url": str(entry.get("url") or ""),
            "source": str(entry.get("source") or ""),
            "message_id": entry.get("message_id", 0),
            "message_date": str(entry.get("message_date") or ""),
            "message_url": str(entry.get("message_url") or ""),
            "message_text": str(entry.get("message_text") or ""),
            "event_id": str(entry.get("event_id") or ""),
        }
    for pending in state.get("pending_posts", {}).values():
        if not isinstance(pending, dict):
            continue
        pending_key = record_wheel_key(pending)
        if normalized == pending_key:
            return {
                "wheel_key": pending_key,
                "identifier": str(pending.get("identifier") or pending_key),
                "url": str(pending.get("url") or ""),
                "source": str(pending.get("source") or ""),
                "message_id": pending.get("message_id", 0),
                "message_date": str(pending.get("message_date") or ""),
                "message_url": str(pending.get("message_url") or ""),
                "message_text": str(pending.get("message_text") or ""),
            }
    return None


def wheel_sources(state: dict[str, Any], key: str, context: dict[str, Any] | None = None) -> list[str]:
    normalized = normalized_wheel_key(key)
    result: list[str] = []
    rows = state.get("wheel_publications", {}).get(normalized, [])
    if isinstance(rows, list):
        result.extend(
            str(row.get("source") or "")
            for row in rows
            if isinstance(row, dict)
        )
    if isinstance(context, dict):
        result.append(str(context.get("source") or ""))
    seen: set[str] = set()
    unique: list[str] = []
    for value in result:
        clean = value.strip().lstrip("@")
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            unique.append(clean)
    return unique


def split_action_value(value: str) -> tuple[str, str]:
    left, separator, right = str(value or "").partition("|")
    return left.strip(), right.strip() if separator else ""


def remove_matching_records(collection: Any, key: str) -> int:
    if not isinstance(collection, dict):
        return 0
    removed = 0
    for record_key in list(collection):
        record = collection.get(record_key)
        direct = normalized_wheel_key(str(record_key))
        related = record_wheel_key(record)
        if key in {direct, related}:
            collection.pop(record_key, None)
            removed += 1
    return removed


def set_manual_deadline(state: dict[str, Any], key: str, deadline_text: str) -> None:
    normalized = normalized_wheel_key(key)
    deadline = monitor.parse_datetime(deadline_text)
    if not normalized or deadline is None:
        raise ValueError("Некорректное колесо или время")
    deadline = deadline.astimezone(monitor.UTC)

    context = wheel_context(state, normalized)
    if context is None:
        raise ValueError("Колесо не найдено")
    active = state.setdefault("active_wheels", {})
    entry = active.get(normalized)
    if not isinstance(entry, dict):
        now = monitor.now_utc()
        entry = {
            "identifier": str(context.get("identifier") or normalized),
            "url": str(context.get("url") or ""),
            "source": str(context.get("source") or "неизвестно"),
            "message_id": int(context.get("message_id", 0) or 0),
            "message_date": str(context.get("message_date") or now.isoformat()),
            "message_url": str(context.get("message_url") or ""),
            "message_text": str(context.get("message_text") or "")[:4000],
            "first_notified_at": now.isoformat(),
            "last_notification_at": now.isoformat(),
        }
        active[normalized] = entry

    entry["deadline"] = deadline.isoformat()
    entry["deadline_source"] = "manual"
    entry["method"] = "время вручную указано администратором BB V.G."
    entry["status"] = "scheduled"
    entry["needs_manual_time"] = False
    entry["last_checked_at"] = monitor.now_utc().isoformat()
    entry["expires_at"] = monitor.participation_expiry(deadline).isoformat()
    wheel_lifecycle_v2.stamp_lifecycle(normalized, entry, monitor.now_utc())

    state.setdefault("manual_deadlines", {})[normalized] = {
        "deadline": deadline.isoformat(),
        "updated_at": monitor.now_utc().isoformat(),
    }
    state.setdefault("completed_wheel_alerts", {}).pop(normalized, None)
    remove_matching_records(state.setdefault("pending_posts", {}), normalized)


def mark_globally_inactive(state: dict[str, Any], key: str, actor: str) -> int:
    normalized = normalized_wheel_key(key)
    if not normalized:
        raise ValueError("Колесо не указано")
    context = wheel_context(state, normalized)
    active_entry = state.get("active_wheels", {}).get(normalized)
    event_entry = active_entry if isinstance(active_entry, dict) else context
    now = monitor.now_utc()
    removed = wheel_lifecycle_v2.mark_inactive_event(
        state,
        normalized,
        event_entry,
        current=now,
        actor=actor or "admin",
    )
    return removed


def apply_action(
    state: dict[str, Any],
    health: dict[str, Any],
    stats: dict[str, Any],
    action: str,
    value: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action": action,
        "value": value,
        "state_changed": False,
        "health_changed": False,
        "stats_changed": False,
        "detail": "",
    }
    if action == "participate_token":
        context = state.get("button_contexts", {}).get(value)
        if not isinstance(context, dict):
            raise ValueError("Контекст кнопки не найден или устарел")
        key = record_wheel_key(context)
        active_entry = state.get("active_wheels", {}).get(key)
        rating_context = active_entry if isinstance(active_entry, dict) else context
        already = wheel_lifecycle_v2.is_admin_confirmed(state, key)
        confirmed = wheel_lifecycle_v2.mark_admin_confirmed(
            state, key, rating_context, monitor.now_utc()
        )
        state.setdefault("participating_wheels", {}).pop(key, None)
        if isinstance(active_entry, dict):
            active_entry.pop("participating", None)
            active_entry.pop("participating_at", None)
            active_entry["admin_confirmed"] = True
            wheel_lifecycle_v2.stamp_lifecycle(key, active_entry, monitor.now_utc())
        result["stats_changed"] = monitor.data_store.record_admin_wheel_decision(
            stats,
            wheel_key=wheel_lifecycle_v2.rating_event_key(key, rating_context),
            sources=wheel_sources(state, key, context),
            decision="confirmed",
            actor="admin",
        )
        result["state_changed"] = confirmed
        result["detail"] = (
            "Колесо уже подтверждено администратором"
            if already
            else "Колесо подтверждено; личные отметки пользователей не изменены"
        )
    elif action == "participate_wheel":
        context = wheel_context(state, value)
        if context is None:
            raise ValueError("Колесо не найдено")
        normalized = normalized_wheel_key(value)
        active_entry = state.get("active_wheels", {}).get(normalized)
        rating_context = active_entry if isinstance(active_entry, dict) else context
        already = wheel_lifecycle_v2.is_admin_confirmed(state, normalized)
        confirmed = wheel_lifecycle_v2.mark_admin_confirmed(
            state, normalized, rating_context, monitor.now_utc()
        )
        state.setdefault("participating_wheels", {}).pop(normalized, None)
        if isinstance(active_entry, dict):
            active_entry.pop("participating", None)
            active_entry.pop("participating_at", None)
            active_entry["admin_confirmed"] = True
            wheel_lifecycle_v2.stamp_lifecycle(
                normalized, active_entry, monitor.now_utc()
            )
        result["stats_changed"] = monitor.data_store.record_admin_wheel_decision(
            stats,
            wheel_key=wheel_lifecycle_v2.rating_event_key(normalized, context),
            sources=wheel_sources(state, value, context),
            decision="confirmed",
            actor="admin",
        )
        result["state_changed"] = confirmed
        result["detail"] = (
            "Колесо уже подтверждено администратором"
            if already
            else "Колесо подтверждено; личные отметки пользователей не изменены"
        )
    elif action == "set_deadline":
        key, deadline_text = split_action_value(value)
        set_manual_deadline(state, key, deadline_text)
        result["state_changed"] = True
        result["detail"] = "Время прокрутки установлено вручную"
    elif action == "mark_inactive_global":
        key, actor = split_action_value(value)
        normalized = normalized_wheel_key(key)
        context = wheel_context(state, key)
        if context is None:
            existing = state.get("inactive_wheels", {}).get(normalized)
            if isinstance(existing, dict):
                result["detail"] = "Колесо уже отмечено неактивным"
                return result
            raise ValueError("Колесо уже отсутствует в активном списке")
        rating_key = wheel_lifecycle_v2.rating_event_key(normalized, context)
        result["stats_changed"] = monitor.data_store.record_admin_wheel_decision(
            stats,
            wheel_key=rating_key,
            sources=wheel_sources(state, key, context),
            decision="inactive",
            actor=actor or "admin",
        )
        removed = mark_globally_inactive(state, key, actor)
        result["state_changed"] = True
        result["detail"] = f"Колесо удалено для всех; очищено записей: {removed}"
    elif action == "remove_active":
        normalized = normalized_wheel_key(value)
        removed = state.setdefault("active_wheels", {}).pop(normalized, None)
        if removed is None:
            for key in list(state.get("active_wheels", {})):
                if str(key).casefold() == normalized:
                    removed = state["active_wheels"].pop(key)
                    break
        result["state_changed"] = removed is not None
        result["detail"] = (
            "Колесо удалено из активного списка" if removed else "Колесо уже отсутствует"
        )
    elif action == "recheck_wheel":
        normalized = normalized_wheel_key(value)
        forced_at = (monitor.now_utc() - timedelta(hours=1)).isoformat()
        matched = 0
        active = state.get("active_wheels", {})
        for key, entry in active.items():
            if not isinstance(entry, dict):
                continue
            identifier = str(entry.get("identifier") or "").casefold()
            if normalized in {str(key).casefold(), identifier}:
                entry["last_checked_at"] = forced_at
                matched += 1
        if not matched:
            raise ValueError("Колесо не найдено")
        result["state_changed"] = True
        result["detail"] = f"Повторная проверка запрошена для {matched} записей"
    elif action == "clear_quarantine":
        sources = health.setdefault("sources", {})
        entry = sources.get(value)
        if not isinstance(entry, dict):
            for source, candidate in sources.items():
                if str(source).casefold() == value.casefold() and isinstance(candidate, dict):
                    entry = candidate
                    value = str(source)
                    break
        if not isinstance(entry, dict):
            raise ValueError("Источник не найден в health-состоянии")
        entry["status"] = "ok"
        entry["consecutive_errors"] = 0
        entry["consecutive_empty"] = 0
        entry.pop("quarantine_until", None)
        entry.pop("last_error", None)
        result["health_changed"] = True
        result["detail"] = f"Карантин @{value} снят"
    else:
        raise ValueError(f"Неизвестное действие: {action}")
    return result


def run_action(action: str, value: str) -> dict[str, Any]:
    state = load_json(STATE_PATH, {})
    health = load_json(HEALTH_PATH, {"version": 1, "sources": {}})
    stats = load_json(STATS_PATH, {"version": 1, "sources": {}, "daily": {}})
    result = apply_action(state, health, stats, action, value)
    if result["state_changed"]:
        save_json(STATE_PATH, state)
    if result["health_changed"]:
        save_json(HEALTH_PATH, health)
    if result.get("stats_changed"):
        save_json(STATS_PATH, stats)
    return result


def self_test() -> None:
    state = {
        "active_wheels": {
            "wheel1": {
                "identifier": "wheel1",
                "url": "https://betboom.ru/freestream/wheel1",
                "source": "test_source",
            }
        },
        "participating_wheels": {},
        "button_contexts": {
            "token1": {
                "wheel_key": "wheel1",
                "identifier": "wheel1",
                "url": "https://betboom.ru/freestream/wheel1",
                "source": "test_source",
            }
        },
        "pending_posts": {},
    }
    health = {
        "sources": {
            "bad": {
                "status": "quarantined",
                "consecutive_errors": 3,
                "last_error": "test",
            }
        }
    }
    stats = {"version": 1, "sources": {}, "daily": {}}
    result = apply_action(state, health, stats, "participate_token", "token1")
    assert result["state_changed"] and "wheel1" in state["admin_confirmed_wheels"]
    assert not state["participating_wheels"]
    assert stats["sources"] and next(iter(stats["sources"].values()))["quality_score"] == 40
    future = (monitor.now_utc() + timedelta(hours=2)).isoformat()
    result = apply_action(state, health, stats, "set_deadline", f"wheel1|{future}")
    assert result["state_changed"] and state["active_wheels"]["wheel1"]["deadline_source"] == "manual"
    result = apply_action(state, health, stats, "clear_quarantine", "bad")
    assert result["health_changed"] and health["sources"]["bad"]["status"] == "ok"
    result = apply_action(state, health, stats, "mark_inactive_global", "wheel1|123")
    assert result["state_changed"] and "wheel1" in state["inactive_wheels"]
    assert "wheel1" not in state["active_wheels"]
    assert next(iter(stats["sources"].values()))["quality_score"] == -45
    print("BB V.G. admin action self-test passed")


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
    result = run_action(args.action, args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
