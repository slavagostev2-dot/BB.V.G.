from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import betboom_auto_participation as auto
import monitor
import telegram_transport

ROOT = Path(__file__).resolve().parent
TRIGGER_PATH = ROOT / "force_auto_participation.trigger"
RESULT_PATH = ROOT / "force_auto_participation_result.json"
SCAN_RESULT_PATH = ROOT / "force_recent_wheels_result.json"


def _json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def _event_token(item: dict[str, Any]) -> str:
    key = str(item.get("wheel_key") or "").casefold()
    action_id = int(item.get("action_id") or 0)
    start = str(item.get("server_start_at") or "")
    if action_id:
        return f"{key}#action:{action_id}:{start}"
    return f"{key}#seen:{item.get('message_date') or ''}"


def _restore_runtime_state(
    state: dict[str, Any],
    active: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    scanned_at: Any,
) -> None:
    attempts_by_key = {
        str(item.get("wheel_key") or "").casefold(): item
        for item in attempts
        if isinstance(item, dict)
    }
    active_wheels = state.setdefault("active_wheels", {})
    participating_wheels = state.setdefault("participating_wheels", {})
    processed = state.setdefault("auto_participation_events", {})

    for item in active:
        key = str(item.get("wheel_key") or "").casefold()
        if not key:
            continue
        existing = active_wheels.get(key)
        is_recovered_missing = not isinstance(existing, dict)
        entry = {} if is_recovered_missing else existing
        if is_recovered_missing:
            active_wheels[key] = entry

        deadline = monitor.parse_datetime(item.get("deadline"))
        expires = (
            deadline + timedelta(minutes=30)
            if deadline is not None
            else scanned_at + timedelta(hours=2)
        )

        if is_recovered_missing:
            entry.update(
                {
                    "identifier": key,
                    "wheel_key": key,
                    "url": str(item.get("url") or ""),
                    "source": str(item.get("source") or ""),
                    "message_id": int(item.get("message_id") or 0),
                    "message_date": item.get("message_date"),
                    "message_url": item.get("message_url"),
                    "method": "восстановлено аварийной проверкой BetBoom",
                }
            )
        else:
            entry.setdefault("identifier", key)
            entry.setdefault("wheel_key", key)
            entry.setdefault("url", str(item.get("url") or ""))

        entry.update(
            {
                "action_id": int(item.get("action_id") or 0),
                "deadline": item.get("deadline"),
                "expires_at": expires.isoformat(),
                "server_start_at": item.get("server_start_at"),
                "page_status": "active",
                "availability_status": "available",
                "verification_status": monitor.WHEEL_VERIFICATION_CONFIRMED,
                "last_checked_at": scanned_at.isoformat(),
                "last_verification_at": scanned_at.isoformat(),
                "needs_manual_time": deadline is None,
            }
        )

        attempt = attempts_by_key.get(key)
        if not isinstance(attempt, dict) or not bool(attempt.get("success")):
            entry.setdefault("participating", False)
            entry.setdefault("lifecycle_state", "active")
            continue

        status = str(attempt.get("status") or "participated")
        if status == "already_marked_participating":
            entry["participating"] = True
            entry["lifecycle_state"] = "participating"
            continue

        entry.update(
            {
                "participating": True,
                "participating_at": scanned_at.isoformat(),
                "lifecycle_state": "participating",
                "auto_participation_status": "participated",
                "auto_participation_checked_at": scanned_at.isoformat(),
                "auto_participation_confirmed_at": scanned_at.isoformat(),
                "auto_participation_retry_allowed": False,
            }
        )
        participating_wheels[key] = {
            "identifier": key,
            "url": str(item.get("url") or ""),
            "deadline": item.get("deadline"),
            "expires_at": expires.isoformat(),
            "marked_at": scanned_at.isoformat(),
            "confirmed_at": scanned_at.isoformat(),
            "participation_source": "betboom_browser_recovery",
            "participation_status": status,
        }
        processed[_event_token(item)] = {
            "wheel_key": key,
            "status": "participated",
            "detail": str(attempt.get("detail") or "BetBoom подтвердил участие")[:300],
            "attempted_at": scanned_at.isoformat(),
            "retry_allowed": False,
            "recovery_scan": True,
        }

    state["last_auto_participation_recovery_scan_at"] = scanned_at.isoformat()
    monitor.save_state(state)


def main() -> int:
    if not auto.configured():
        raise SystemExit("BetBoom auto participation session is not configured")

    telegram_transport.install(monitor)
    sources = monitor.read_list(monitor.SOURCES_PATH)
    results, errors, empty = monitor.fetch_all_sources(sources)
    now = monitor.now_utc()
    cutoff = now - timedelta(hours=3)

    persisted = _json(monitor.STATE_PATH, {})
    participating = {
        str(key).casefold()
        for key, value in (persisted.get("participating_wheels") or {}).items()
        if isinstance(value, dict)
    }

    candidates: dict[str, dict[str, Any]] = {}
    for source, messages in results.items():
        if not isinstance(messages, list):
            continue
        for message in messages:
            try:
                published = message.date.astimezone(monitor.UTC)
            except Exception:
                continue
            if published < cutoff:
                continue
            for link in monitor.extract_links(message.text):
                key = monitor.wheel_key(link)
                current = candidates.get(key)
                record = {
                    "wheel_key": key,
                    "url": monitor.normalize_url(link),
                    "source": source,
                    "message_id": message.message_id,
                    "message_date": published.isoformat(),
                    "message_url": message.message_url,
                }
                if current is None or record["message_date"] > current["message_date"]:
                    candidates[key] = record

    checked: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for record in sorted(candidates.values(), key=lambda item: item["message_date"], reverse=True):
        inspection = monitor.inspect_wheel_page(record["url"])
        item = dict(record)
        item.update(
            api_status=inspection.status,
            action_id=inspection.action_id,
            deadline=inspection.deadline.isoformat() if inspection.deadline else None,
            server_start_at=inspection.server_start_at.isoformat() if inspection.server_start_at else None,
        )
        checked.append(item)
        if inspection.status == "active":
            active.append(item)

    attempts: list[dict[str, Any]] = []
    original_trigger = TRIGGER_PATH.read_text(encoding="utf-8") if TRIGGER_PATH.exists() else ""
    try:
        for item in active:
            key = str(item["wheel_key"]).casefold()
            if key in participating:
                attempts.append({**item, "success": True, "status": "already_marked_participating"})
                continue

            TRIGGER_PATH.write_text(str(item["url"]) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "force_auto_participation_browser.py"],
                cwd=str(ROOT),
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            browser_result = _json(RESULT_PATH, {})
            attempt = {
                **item,
                "success": bool(browser_result.get("success")),
                "status": str(browser_result.get("status") or f"exit_{completed.returncode}"),
                "detail": str(browser_result.get("detail") or "")[:300],
            }
            attempts.append(attempt)
    finally:
        if original_trigger:
            TRIGGER_PATH.write_text(original_trigger, encoding="utf-8")
        else:
            TRIGGER_PATH.unlink(missing_ok=True)

    _restore_runtime_state(persisted, active, attempts, now)

    payload = {
        "scanned_at": now.isoformat(),
        "sources_total": len(sources),
        "sources_ok": len(results),
        "source_errors": len(errors),
        "source_empty": len(empty),
        "fresh_candidates": len(candidates),
        "active_candidates": len(active),
        "checked": checked,
        "attempts": attempts,
        "successful_urls": [item["url"] for item in attempts if item.get("success")],
    }
    SCAN_RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    RESULT_PATH.write_text(
        json.dumps(
            {
                "success": any(item.get("success") for item in attempts),
                "status": "recent_scan_completed",
                "attempts": attempts,
                "scanned_at": now.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if any(item.get("success") for item in attempts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
