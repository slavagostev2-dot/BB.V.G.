from __future__ import annotations

import html
import json
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import monitor
import monitor_data as data_store
import telegram_transport

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "intelligence_state.json"
ACTIVE_PATH = ROOT / "public_sources.txt"
NIGHTLY_PATH = ROOT / "source_catalog.txt"
MODERATION_PATH = ROOT / "candidate_moderation.json"

UTC = timezone.utc
SOURCE_LIMIT = max(5, int(os.getenv("INTELLIGENCE_SOURCE_LIMIT", "150")))
CANDIDATE_LIMIT = max(10, int(os.getenv("INTELLIGENCE_CANDIDATE_LIMIT", "100")))
VERIFY_LIMIT = max(5, int(os.getenv("INTELLIGENCE_VERIFY_LIMIT", "40")))
INTELLIGENCE_WORKERS = max(2, min(16, int(os.getenv("INTELLIGENCE_WORKERS", "12"))))

USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{4,31})")
TME_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?",
    re.IGNORECASE,
)

RESERVED = {
    "share", "joinchat", "addstickers", "proxy", "socks", "login", "iv",
    "telegram", "telegramtips", "durov", "betboom", "freestream",
}

# A reference is a source candidate only when the surrounding publication or
# the username itself contains a wheel-adjacent signal.  A bare @mention is
# usually a person, a support account or a bot and must not enter discovery.
CONTEXT_RADIUS = 260
THEME_PATTERNS: dict[str, re.Pattern[str]] = {
    "колёса и акции": re.compile(
        r"(?:betboom|free\s*stream|freestream|кол[её]с|wheel|freebet|фрибет|"
        r"розыгрыш|giveaway|промокод|бонус)",
        re.IGNORECASE,
    ),
    "ставки": re.compile(
        r"(?:ставк|betting|wager|bookmaker|букмекер|fonbet)",
        re.IGNORECASE,
    ),
    "стримы": re.compile(
        r"(?:стрим|stream|twitch|трансляц|прямой\s+эфир)",
        re.IGNORECASE,
    ),
    "киберспорт и игры": re.compile(
        r"(?:киберспорт|esports?|dota|cs[ _-]?2|counter[ _-]?strike|pubg|"
        r"valorant|mobile\s+legends|mlbb|world\s+of\s+tanks|\bwot\b|"
        r"покер|poker|турнир)",
        re.IGNORECASE,
    ),
}
USERNAME_THEME_PATTERNS: dict[str, re.Pattern[str]] = {
    "ставки": re.compile(r"(?:bet|wager|stavka|fonbet)", re.IGNORECASE),
    "стримы": re.compile(r"(?:stream|twitch|cast)", re.IGNORECASE),
    "киберспорт и игры": re.compile(
        r"(?:esport|gaming|dota|cs2|poker|pubg|valorant|mlbb|wot)",
        re.IGNORECASE,
    ),
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
    data_store.atomic_write_json(STATE_PATH, value)


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


def is_bot_username(username: str) -> bool:
    """Telegram bot usernames are required to end in ``bot``."""
    return username.strip().lstrip("@").casefold().endswith("bot")


def thematic_signals(text: str) -> set[str]:
    return {
        label for label, pattern in THEME_PATTERNS.items() if pattern.search(text)
    }


def username_signals(username: str) -> set[str]:
    return {
        label
        for label, pattern in USERNAME_THEME_PATTERNS.items()
        if pattern.search(username)
    }


def reference_candidates(text: str) -> dict[str, set[str]]:
    """Return only thematic, non-bot Telegram references from a publication."""
    matches = [*USERNAME_RE.finditer(text), *TME_RE.finditer(text)]
    found: dict[str, tuple[str, set[str]]] = {}
    for match in matches:
        source = match.group(1)
        key = source.casefold()
        if key in RESERVED or source.isdigit() or is_bot_username(source):
            continue
        start = max(0, match.start() - CONTEXT_RADIUS)
        end = min(len(text), match.end() + CONTEXT_RADIUS)
        context = text[start:end]
        # Keywords inside another @username are not publication context.
        context = USERNAME_RE.sub(" ", context)
        context = TME_RE.sub(" ", context)
        signals = thematic_signals(context) | username_signals(source)
        if not signals:
            continue
        previous = found.get(key)
        if previous is None:
            found[key] = (source, signals)
        else:
            previous[1].update(signals)
    return {source: signals for source, signals in found.values()}


def extract_references(text: str) -> set[str]:
    return set(reference_candidates(text))


def candidate_is_relevant(entry: dict[str, Any]) -> bool:
    if is_bot_username(str(entry.get("source") or "")):
        return False
    if int(entry.get("wheel_links_found", 0) or 0) > 0:
        return True
    if int(entry.get("relevant_messages_found", 0) or 0) > 0:
        return True
    return bool(entry.get("context_signals") or entry.get("username_signals"))


def candidate_is_nightly_eligible(entry: dict[str, Any]) -> bool:
    return bool(
        entry.get("public") is True
        and entry.get("status") == "ok"
        and entry.get("relevance_status") == "relevant"
        and candidate_is_relevant(entry)
    )


def verify_candidate(username: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "public": False,
        "messages_checked": 0,
        "wheel_links_found": 0,
        "latest_wheel_at": None,
        "sample_wheels": [],
        "status": "unknown",
        "relevant_messages_found": 0,
        "candidate_signals": [],
    }
    if is_bot_username(username):
        result["status"] = "bot"
        result["relevance_status"] = "irrelevant"
        return result
    try:
        messages = monitor.fetch_public_channel(username)
    except Exception as exc:
        failure_code, failure_reason = data_store.classify_source_problem(
            "error", f"{type(exc).__name__}: {exc}"
        )
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
        result["failure_code"] = failure_code
        result["failure_reason"] = failure_reason
        return result
    if not messages:
        result["status"] = "empty"
        return result
    result["public"] = True
    result["status"] = "ok"
    result["messages_checked"] = len(messages)
    wheels: list[tuple[monitor.Message, str]] = []
    own_signals: set[str] = set()
    relevant_messages = 0
    for message in messages:
        message_signals = thematic_signals(message.text)
        if message_signals:
            relevant_messages += 1
            own_signals.update(message_signals)
        for link in monitor.extract_links(message.text):
            wheels.append((message, link))
    result["relevant_messages_found"] = relevant_messages
    result["candidate_signals"] = sorted(own_signals)
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
    if is_bot_username(str(entry.get("source") or "")):
        return 0
    refs = len(entry.get("discovered_from", []))
    mentions = int(entry.get("mention_count", 0) or 0)
    wheels = int(entry.get("wheel_links_found", 0) or 0)
    relevant_messages = int(entry.get("relevant_messages_found", 0) or 0)
    context_signals = len(entry.get("context_signals", []) or [])
    own_signals = len(entry.get("candidate_signals", []) or [])
    score = (
        min(15, refs * 5)
        + min(10, mentions * 2)
        + min(45, wheels * 15)
        + min(20, relevant_messages * 4)
        + min(10, (context_signals + own_signals) * 3)
    )
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
        lambda: {
            "mention_count": 0,
            "discovered_from": set(),
            "context_signals": set(),
            "evidence": [],
        }
    )
    scanned = 0
    errors: list[str] = []
    error_types: dict[str, int] = defaultdict(int)

    selected_sources = sources[:SOURCE_LIMIT]
    # Reuse the permanent monitor's resilient batch transport. A systemic
    # telegram.me outage is retried as one transport incident instead of
    # degrading every configured channel independently.
    scan_results, scan_errors, empty_sources = monitor.fetch_all_sources(selected_sources)
    for source in empty_sources:
        scan_results[source] = []
    for source, detail in scan_errors.items():
        failure_code, failure_reason = data_store.classify_source_problem(
            "error", detail
        )
        error_types[failure_code] += 1
        errors.append(f"@{source}: {failure_code} — {failure_reason}")

    for source in selected_sources:
        messages = scan_results.get(source)
        if messages is None:
            continue
        scanned += 1
        for message in messages:
            for candidate, signals in reference_candidates(message.text).items():
                key = candidate.casefold()
                if key in known or key in ignored or key == source.casefold():
                    continue
                item = discovered[key]
                item["source"] = candidate
                item["mention_count"] += 1
                item["discovered_from"].add(source)
                item["context_signals"].update(signals)
                if len(item["evidence"]) < 8:
                    item["evidence"].append({
                        "from": source,
                        "message_url": message.message_url,
                        "published_at": message.date.astimezone(UTC).isoformat(),
                        "method": "тематическое упоминание или ссылка Telegram",
                        "signals": sorted(signals),
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

    verification_results: dict[str, dict[str, Any]] = {}
    verify_sources = [str(raw["source"]) for raw in ordered[:VERIFY_LIMIT]]
    with ThreadPoolExecutor(
        max_workers=min(INTELLIGENCE_WORKERS, max(1, len(verify_sources)))
    ) as pool:
        futures = {pool.submit(verify_candidate, source): source for source in verify_sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                verification_results[source.casefold()] = future.result()
            except Exception as exc:
                verification_results[source.casefold()] = {
                    "public": False,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }

    verified = len(verification_results)
    strong = 0
    current_keys = {str(raw["source"]).casefold() for raw in ordered}
    for key, raw in list(state["candidates"].items()):
        if not isinstance(raw, dict):
            continue
        if is_bot_username(str(raw.get("source") or key)):
            raw["relevance_status"] = "irrelevant"
            raw["score"] = 0
        elif key not in current_keys and "relevance_status" not in raw:
            # Old broad-regex results remain auditable in state but disappear
            # from the actionable queue until found again by the new filter.
            raw["relevance_status"] = "legacy_unclassified"
    for raw in ordered:
        source = str(raw["source"])
        key = source.casefold()
        previous = state["candidates"].get(key, {})
        entry = dict(previous) if isinstance(previous, dict) else {}
        entry.update({
            "source": source,
            "mention_count": int(raw["mention_count"]),
            "discovered_from": sorted(raw["discovered_from"], key=str.casefold),
            "context_signals": sorted(raw["context_signals"]),
            "username_signals": sorted(username_signals(source)),
            "evidence": raw["evidence"],
            "last_discovered_at": now_iso(),
        })
        entry.setdefault("first_discovered_at", now_iso())
        if key in verification_results:
            entry.update(verification_results[key])
            entry["last_verified_at"] = now_iso()
        entry["relevance_status"] = (
            "relevant" if candidate_is_relevant(entry) else "irrelevant"
        )
        entry["score"] = score_candidate(entry)
        if int(entry["score"]) >= 60:
            strong += 1
        state["candidates"][key] = entry

    state["last_run_at"] = now_iso()
    state["telegram_domain"] = telegram_transport.PRIMARY_DOMAIN
    state["last_run_summary"] = {
        "known_sources": len(sources),
        "sources_scanned": scanned,
        "references_found": sum(int(item["mention_count"]) for item in ordered),
        "unique_candidates": len(ordered),
        "relevant_candidates": sum(
            1
            for item in ordered
            if state["candidates"].get(str(item["source"]).casefold(), {}).get(
                "relevance_status"
            )
            == "relevant"
        ),
        "verified_candidates": verified,
        "strong_candidates": strong,
        "errors": len(errors),
        "empty_sources": len(empty_sources),
        "error_types": dict(sorted(error_types.items())),
        "workers": INTELLIGENCE_WORKERS,
        "telegram_domain": telegram_transport.PRIMARY_DOMAIN,
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
