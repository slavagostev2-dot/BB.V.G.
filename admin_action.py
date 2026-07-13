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


def wheel_context(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    normalized = key.casefold()
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
        }
    for pending in state.get("pending_posts", {}).values():
        if not isinstance(pending, dict):
            continue
        identifier = str(pending.get("identifier") or "").casefold()
        url = str(pending.get("url") or "")
        try:
            pending_key = monitor.wheel_key(url) if url else identifier
        except Exception:
            pending_key = identifier
        if normalized in {identifier, pending_key.casefold()}:
            return {
                "wheel_key": pending_key.casefold(),
                "identifier": str(pending.get("identifier") or pending_key),
                "url": url,
            }
    return None


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
    elif action == "remove_active":
        normalized = value.casefold()
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
        normalized = value.casefold()
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
        for entry in state.get("pending_posts", {}).values():
            if not isinstance(entry, dict):
                continue
            identifier = str(entry.get("identifier") or "").casefold()
            url = str(entry.get("url") or "")
            try:
                pending_key = monitor.wheel_key(url).casefold() if url else identifier
            except Exception:
                pending_key = identifier
            if normalized in {identifier, pending_key}:
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
    result = apply_action(state, health, "recheck_wheel", "wheel1")
    assert result["state_changed"]
    result = apply_action(state, health, "clear_quarantine", "bad")
    assert result["health_changed"] and health["sources"]["bad"]["status"] == "ok"
    result = apply_action(state, health, "remove_active", "wheel1")
    assert result["state_changed"] and not state["active_wheels"]
    print("admin_action self-test passed")


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
