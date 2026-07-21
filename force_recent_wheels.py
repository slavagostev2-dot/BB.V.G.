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
        TRIGGER_PATH.write_text(original_trigger, encoding="utf-8")

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
