from __future__ import annotations

import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import monitor
import monitor_data as data_store
from nightly_discovery import fetch_public_channel_page

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "intelligence_state.json"
ACTIVE_PATH = ROOT / "public_sources.txt"
NIGHTLY_PATH = ROOT / "source_catalog.txt"
MODERATION_PATH = ROOT / "candidate_moderation.json"

UTC = timezone.utc
SOURCE_LIMIT = max(5, int(os.getenv("INTELLIGENCE_SOURCE_LIMIT", "70")))
CANDIDATE_LIMIT = max(10, int(os.getenv("INTELLIGENCE_CANDIDATE_LIMIT", "100")))
VERIFY_LIMIT = max(5, int(os.getenv("INTELLIGENCE_VERIFY_LIMIT", "40")))

USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{4,31})")
TME_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?",
    re.IGNORECASE,
)

RESERVED = {
    "share", "joinchat", "addstickers", "proxy", "socks", "login", "iv",
    "telegram", "telegramtips", "durov", "betboom", "freestream",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_state() -> dict[str, Any]:
    value = read_json(STATE_PATH, {})
    if not isinstance(value, dict):
        value = {}
    value.setdefault("version", 1)
    value.setdefault("candidates", {})
    value.setdefault("edges", {})
    value.setdefault("runs", [])
    return value


def save_state(value: dict[str, Any]) -> None:
    value["version"] = 1
    value["updated_at"] = now_iso()
    value["runs"] = list(value.get("runs", []))[-30:]
    STATE_PATH.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def known_sources() -> tuple[list[str], set[str]]:
    active = monitor.read_list(ACTIVE_PATH)
    nightly = monitor.read_list(NIGHTLY_PATH)
    ordered: list[str] = []
    seen: set[str] = set()
    for source in [*active, *nightly]:
        clean = source.strip().lstrip("@")
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            ordered.append(clean)
    return ordered, seen


def ignored_sources() -> set[str]:
    value = read_json(MODERATION_PATH, {})
    ignored = value.get("ignored", {}) if isinstance(value, dict) else {}
    return {str(source).casefold() for source in ignored if str(source)} if isinstance(ignored, dict) else set()


def extract_references(text: str) -> set[str]:
    found = {match.group(1) for match in USERNAME_RE.finditer(text)}
    found.update(match.group(1) for match in TME_RE.finditer(text))
    return {
        value
        for value in found
        if value.casefold() not in RESERVED and not value.isdigit()
    }


def verify_candidate(username: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "public": False,
        "messages_checked": 0,
        "wheel_links_found": 0,
        "latest_wheel_at": None,
        "sample_wheels": [],
        "status": "unknown",
    }
    try:
        messages = fetch_public_channel_page(username)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return result
    if not messages:
        result["status"] = "empty"
        return result
    result["public"] = True
    result["status"] = "ok"
    result["messages_checked"] = len(messages)
    wheels: list[tuple[monitor.Message, str]] = []
    for message in messages:
        for link in monitor.extract_links(message.text):
            wheels.append((message, link))
    result["wheel_links_found"] = len(wheels)
    if wheels:
        latest = max(wheels, key=lambda item: item[0].date)
        result["latest_wheel_at"] = latest[0].date.astimezone(UTC).isoformat()
        samples = []
        for message, link in sorted(wheels, key=lambda item: item[0].date, reverse=True)[:5]:
            samples.append({
                "identifier": monitor.wheel_identifier(link),
                "url": monitor.normalize_url(link),
                "message_url": message.message_url,
                "published_at": message.date.astimezone(UTC).isoformat(),
            })
        result["sample_wheels"] = samples
    return result


def score_candidate(entry: dict[str, Any]) -> int:
    refs = len(entry.get("discovered_from", []))
    mentions = int(entry.get("mention_count", 0) or 0)
    wheels = int(entry.get("wheel_links_found", 0) or 0)
    score = min(30, refs * 10) + min(20, mentions * 3) + min(45, wheels * 15)
    if entry.get("public"):
        score += 5
    if entry.get("status") in {"error", "empty"}:
        score -= 25
    return max(0, min(100, score))


def main() -> int:
    sources, known = known_sources()
    ignored = ignored_sources()
    state = load_state()
    discovered: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"mention_count": 0, "discovered_from": set(), "evidence": []}
    )
    scanned = 0
    errors: list[str] = []

    for source in sources[:SOURCE_LIMIT]:
        try:
            messages = fetch_public_channel_page(source)
        except Exception as exc:
            errors.append(f"@{source}: {type(exc).__name__}")
            continue
        scanned += 1
        for message in messages:
            for candidate in extract_references(message.text):
                key = candidate.casefold()
                if key in known or key in ignored or key == source.casefold():
                    continue
                item = discovered[key]
                item["source"] = candidate
                item["mention_count"] += 1
                item["discovered_from"].add(source)
                if len(item["evidence"]) < 8:
                    item["evidence"].append({
                        "from": source,
                        "message_url": message.message_url,
                        "published_at": message.date.astimezone(UTC).isoformat(),
                        "method": "упоминание или ссылка Telegram",
                    })
                edge_key = f"{source.casefold()}->{key}"
                edge = state["edges"].setdefault(edge_key, {
                    "from": source,
                    "to": candidate,
                    "count": 0,
                    "first_seen_at": now_iso(),
                })
                edge["count"] = int(edge.get("count", 0)) + 1
                edge["last_seen_at"] = now_iso()

    ordered = sorted(
        discovered.values(),
        key=lambda item: (-int(item["mention_count"]), -len(item["discovered_from"]), str(item["source"]).casefold()),
    )[:CANDIDATE_LIMIT]

    verified = 0
    strong = 0
    for raw in ordered:
        source = str(raw["source"])
        key = source.casefold()
        previous = state["candidates"].get(key, {})
        entry = dict(previous) if isinstance(previous, dict) else {}
        entry.update({
            "source": source,
            "mention_count": int(raw["mention_count"]),
            "discovered_from": sorted(raw["discovered_from"], key=str.casefold),
            "evidence": raw["evidence"],
            "last_discovered_at": now_iso(),
        })
        entry.setdefault("first_discovered_at", now_iso())
        if verified < VERIFY_LIMIT:
            entry.update(verify_candidate(source))
            entry["last_verified_at"] = now_iso()
            verified += 1
        entry["score"] = score_candidate(entry)
        if int(entry["score"]) >= 60:
            strong += 1
        state["candidates"][key] = entry

    state["last_run_at"] = now_iso()
    state["last_run_summary"] = {
        "known_sources": len(sources),
        "sources_scanned": scanned,
        "references_found": sum(int(item["mention_count"]) for item in ordered),
        "unique_candidates": len(ordered),
        "verified_candidates": verified,
        "strong_candidates": strong,
        "errors": len(errors),
    }
    state["last_errors"] = errors[:30]
    state["runs"].append({"at": now_iso(), **state["last_run_summary"]})
    save_state(state)

    print(
        f"Scanned {scanned}/{len(sources)} known sources; candidates={len(ordered)}; "
        f"verified={verified}; strong={strong}; errors={len(errors)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
