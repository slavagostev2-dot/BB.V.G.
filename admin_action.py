from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import monitor


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
HEALTH_PATH = ROOT / "source_health.json"


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
            "participating": monitor.is_participating(state, normalized),
        }
        active[normalized] = entry

    entry["deadline"] = deadline.isoformat()
    entry["deadline_source"] = "manual"
    entry["method"] = "время вручную указано администратором BB V.G."
    entry["status"] = "scheduled"
    entry["needs_manual_time"] = False
    entry["last_checked_at"] = monitor.now_utc().isoformat()
    entry["expires_at"] = monitor.participation_expiry(deadline).isoformat()

    state.setdefault("manual_deadlines", {})[normalized] = {
        "deadline": deadline.isoformat(),
        "updated_at": monitor.now_utc().isoformat(),
    }
    participant = state.setdefault("participating_wheels", {}).get(normalized)
    if isinstance(participant, dict):
        participant["deadline"] = deadline.isoformat()
        participant["expires_at"] = monitor.participation_expiry(deadline).isoformat()
    state.setdefault("completed_wheel_alerts", {}).pop(normalized, None)
    remove_matching_records(state.setdefault("pending_posts", {}), normalized)


def mark_globally_inactive(state: dict[str, Any], key: str, actor: str) -> int:
    normalized = normalized_wheel_key(key)
    if not normalized:
        raise ValueError("Колесо не указано")

    removed = 0
    for name in (
        "active_wheels",
        "participating_wheels",
        "pending_posts",
        "button_contexts",
        "url_alerts",
        "activation_alerts",
        "completed_wheel_alerts",
        "manual_deadlines",
    ):
        removed += remove_matching_records(state.setdefault(name, {}), normalized)

    now = monitor.now_utc()
    state.setdefault("inactive_wheels", {})[normalized] = {
        "identifier": normalized,
        "marked_at": now.isoformat(),
        "marked_by": str(actor or "admin"),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    }
    return removed


def apply_action(
    state: dict[str, Any],
    health: dict[str, Any],
    action: str,
    value: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action": action,
        "value": value,
        "state_changed": False,
        "health_changed": False,
        "detail": "",
    }
    if action == "participate_token":
        context = state.get("button_contexts", {}).get(value)
        if not isinstance(context, dict):
            raise ValueError("Контекст кнопки не найден или устарел")
        monitor.mark_participating(state, context)
        result["state_changed"] = True
        result["detail"] = "Участие отмечено"
    elif action == "participate_wheel":
        context = wheel_context(state, value)
        if context is None:
            raise ValueError("Колесо не найдено")
        monitor.mark_participating(state, context)
        result["state_changed"] = True
        result["detail"] = "Участие отмечено"
    elif action == "set_deadline":
        key, deadline_text = split_action_value(value)
        set_manual_deadline(state, key, deadline_text)
        result["state_changed"] = True
        result["detail"] = "Время прокрутки установлено вручную"
    elif action == "mark_inactive_global":
        key, actor = split_action_value(value)
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
    result = apply_action(state, health, action, value)
    if result["state_changed"]:
        save_json(STATE_PATH, state)
    if result["health_changed"]:
        save_json(HEALTH_PATH, health)
    return result


def self_test() -> None:
    state = {
        "active_wheels": {
            "wheel1": {
                "identifier": "wheel1",
                "url": "https://betboom.ru/freestream/wheel1",
            }
        },
        "participating_wheels": {},
        "button_contexts": {
            "token1": {
                "wheel_key": "wheel1",
                "identifier": "wheel1",
                "url": "https://betboom.ru/freestream/wheel1",
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
    result = apply_action(state, health, "participate_token", "token1")
    assert result["state_changed"] and "wheel1" in state["participating_wheels"]
    future = (monitor.now_utc() + timedelta(hours=2)).isoformat()
    result = apply_action(state, health, "set_deadline", f"wheel1|{future}")
    assert result["state_changed"] and state["active_wheels"]["wheel1"]["deadline_source"] == "manual"
    result = apply_action(state, health, "clear_quarantine", "bad")
    assert result["health_changed"] and health["sources"]["bad"]["status"] == "ok"
    result = apply_action(state, health, "mark_inactive_global", "wheel1|123")
    assert result["state_changed"] and "wheel1" in state["inactive_wheels"]
    assert "wheel1" not in state["active_wheels"]
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
