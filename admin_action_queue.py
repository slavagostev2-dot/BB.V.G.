from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

import admin_action_v3


ROOT = Path(__file__).resolve().parent
QUEUE_PATH = ROOT / "admin_action_queue.json"
FORMAT = "bbvg-admin-action-queue-v1"
UTC = timezone.utc
MAX_COMMANDS = max(100, int(os.getenv("ADMIN_ACTION_QUEUE_MAX", "1000")))
MAX_APPLIED = max(100, int(os.getenv("ADMIN_ACTION_APPLIED_MAX", "2000")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
ALLOWED_ACTIONS = {
    "participate_token",
    "participate_wheel",
    "mark_inactive_global",
    "confirm_finished_global",
    "set_deadline",
    "remove_active",
    "recheck_wheel",
    "clear_quarantine",
}
COMMAND_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def now_utc() -> datetime:
    return datetime.now(UTC)


def default_queue() -> dict[str, Any]:
    return {"format": FORMAT, "sequence": 0, "commands": {}}


def normalize_queue(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    commands = raw.get("commands") if isinstance(raw.get("commands"), dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for command_id, entry in commands.items():
        key = str(command_id)
        if not COMMAND_ID_RE.fullmatch(key) or not isinstance(entry, dict):
            continue
        action = str(entry.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            continue
        try:
            sequence = max(1, int(entry.get("sequence", 0) or 0))
        except (TypeError, ValueError):
            continue
        normalized[key] = {
            "sequence": sequence,
            "action": action,
            "value": str(entry.get("value") or "")[:1200],
            "created_at": str(entry.get("created_at") or ""),
        }
    ordered = sorted(
        normalized.items(), key=lambda item: (item[1]["sequence"], item[0])
    )[-MAX_COMMANDS:]
    sequence = max(
        [int(raw.get("sequence", 0) or 0)]
        + [int(entry["sequence"]) for _, entry in ordered]
    )
    return {"format": FORMAT, "sequence": sequence, "commands": dict(ordered)}


def _safe_value(action: str, value: str) -> str:
    clean = str(value or "").strip()[:1200]
    if not clean:
        raise ValueError("Значение административного действия не указано")
    if action in {"mark_inactive_global", "confirm_finished_global"}:
        wheel, _, _actor = clean.partition("|")
        clean = f"{wheel.strip()}|admin"
    return clean


def append_command(
    queue: dict[str, Any],
    action: str,
    value: str,
    *,
    command_id: str | None = None,
    created_at: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Неподдерживаемое административное действие: {action}")
    result = normalize_queue(queue)
    key = str(command_id or uuid.uuid4().hex)
    if not COMMAND_ID_RE.fullmatch(key):
        raise ValueError("Некорректный идентификатор команды")
    if key in result["commands"]:
        return result, key
    sequence = int(result["sequence"]) + 1
    result["sequence"] = sequence
    result["commands"][key] = {
        "sequence": sequence,
        "action": action,
        "value": _safe_value(action, value),
        "created_at": (created_at or now_utc()).astimezone(UTC).isoformat(),
    }
    return normalize_queue(result), key


def load_local_queue(path: Path | None = None) -> dict[str, Any]:
    target = path or QUEUE_PATH
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default_queue()
    return normalize_queue(value)


def load_remote_queue() -> dict[str, Any]:
    token = str(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    repository = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    branch = str(os.getenv("GITHUB_BRANCH") or "main").strip() or "main"
    if not token or not repository:
        return load_local_queue()
    api = str(os.getenv("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    response = requests.get(
        f"{api}/repos/{repository}/contents/{quote(QUEUE_PATH.name)}",
        params={"ref": branch},
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    content = base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
    return normalize_queue(json.loads(content))


def enqueue_remote(action: str, value: str) -> str:
    token = str(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    repository = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    branch = str(os.getenv("GITHUB_BRANCH") or "main").strip() or "main"
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")
    api = str(os.getenv("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    url = f"{api}/repos/{repository}/contents/{quote(QUEUE_PATH.name)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    command_id = uuid.uuid4().hex
    last_error: Exception | None = None
    for _attempt in range(1, 5):
        response = requests.get(
            url,
            params={"ref": branch},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        content = base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
        queue, _ = append_command(
            normalize_queue(json.loads(content)),
            action,
            value,
            command_id=command_id,
        )
        updated = requests.put(
            url,
            headers=headers,
            json={
                "message": f"Queue BB V.G. administrator action: {action} [skip ci]",
                "content": base64.b64encode(
                    (json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                        "utf-8"
                    )
                ).decode("ascii"),
                "sha": str(payload.get("sha") or ""),
                "branch": branch,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if updated.status_code in {200, 201}:
            return command_id
        if updated.status_code in {409, 422}:
            last_error = RuntimeError(f"Concurrent queue update: HTTP {updated.status_code}")
            continue
        updated.raise_for_status()
    raise RuntimeError("Не удалось поставить действие в очередь после четырёх попыток") from last_error


def _bounded_mapping(value: Any, maximum: int) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return dict(list(raw.items())[-maximum:])


def process_pending(
    state: dict[str, Any],
    health: dict[str, Any],
    stats: dict[str, Any],
    *,
    queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "loaded": 0,
        "pending": 0,
        "applied": 0,
        "failed": 0,
        "changed": False,
    }
    try:
        current = normalize_queue(queue) if queue is not None else load_remote_queue()
    except Exception as exc:
        summary["failed"] = 1
        summary["error"] = f"queue_load:{type(exc).__name__}"
        return summary

    commands = current["commands"]
    summary["loaded"] = len(commands)
    applied = _bounded_mapping(state.get("applied_admin_actions"), MAX_APPLIED)
    results = _bounded_mapping(state.get("admin_action_results"), MAX_APPLIED)
    attempts = _bounded_mapping(state.get("admin_action_attempts"), MAX_APPLIED)
    pending = [
        (command_id, entry)
        for command_id, entry in commands.items()
        if command_id not in applied
    ]
    pending.sort(key=lambda item: (int(item[1]["sequence"]), item[0]))
    summary["pending"] = len(pending)

    for command_id, entry in pending:
        action = str(entry["action"])
        try:
            result = admin_action_v3.apply_action_v3(
                state, health, stats, action, str(entry["value"])
            )
        except Exception as exc:
            count = max(0, int(attempts.get(command_id, 0) or 0)) + 1
            attempts[command_id] = count
            results[command_id] = {
                "status": "failed",
                "action": action,
                "attempts": count,
                "error_type": type(exc).__name__,
                "last_attempt_at": now_utc().isoformat(),
            }
            summary["failed"] += 1
            summary["changed"] = True
            continue

        timestamp = now_utc().isoformat()
        applied[command_id] = timestamp
        attempts.pop(command_id, None)
        results[command_id] = {
            "status": "applied",
            "action": action,
            "applied_at": timestamp,
            "state_changed": bool(result.get("state_changed")),
            "health_changed": bool(result.get("health_changed")),
            "stats_changed": bool(result.get("stats_changed")),
        }
        summary["applied"] += 1
        summary["changed"] = True

    state["applied_admin_actions"] = _bounded_mapping(applied, MAX_APPLIED)
    state["admin_action_results"] = _bounded_mapping(results, MAX_APPLIED)
    state["admin_action_attempts"] = _bounded_mapping(attempts, MAX_APPLIED)
    if summary["applied"]:
        state["last_admin_action_applied_at"] = now_utc().isoformat()
    return summary


def self_test() -> None:
    queue, command_id = append_command(
        default_queue(),
        "mark_inactive_global",
        "wheel-a|123456789",
        command_id="chapter1-command",
    )
    serialized = json.dumps(queue, ensure_ascii=False)
    assert "123456789" not in serialized
    assert queue["commands"][command_id]["value"] == "wheel-a|admin"

    state = {
        "active_wheels": {"wheel-a": {"identifier": "wheel-a", "source": "test"}},
        "participating_wheels": {},
    }
    health: dict[str, Any] = {"sources": {}}
    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
    first = process_pending(state, health, stats, queue=queue)
    second = process_pending(state, health, stats, queue=queue)
    assert first["applied"] == 1
    assert second["applied"] == 0
    assert command_id in state["applied_admin_actions"]
    assert "wheel-a" not in state["active_wheels"]
    print("admin action queue self-test passed")


if __name__ == "__main__":
    self_test()
