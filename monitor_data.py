from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
PARTNERS_CATALOG_PATH = ROOT / "partners_catalog.json"
SOURCE_HEALTH_PATH = ROOT / "source_health.json"
SOURCE_STATS_PATH = ROOT / "source_stats.json"
UNKNOWN_TIMER_PATH = ROOT / "unknown_timer_samples.json"
PUBLIC_SOURCES_PATH = ROOT / "public_sources.txt"
NIGHTLY_SOURCES_PATH = ROOT / "source_catalog.txt"

QUARANTINE_FAILURE_THRESHOLD = max(
    1, int(os.getenv("QUARANTINE_FAILURE_THRESHOLD", "3"))
)
QUARANTINE_EMPTY_THRESHOLD = max(
    1, int(os.getenv("QUARANTINE_EMPTY_THRESHOLD", "4"))
)
QUARANTINE_RECHECK_HOURS = max(
    1, int(os.getenv("QUARANTINE_RECHECK_HOURS", "6"))
)
UNAVAILABLE_REPORT_DAYS = max(
    1, int(os.getenv("UNAVAILABLE_REPORT_DAYS", "2"))
)
UNKNOWN_TIMER_LIMIT = max(20, int(os.getenv("UNKNOWN_TIMER_LIMIT", "250")))
STATS_RETENTION_DAYS = max(30, int(os.getenv("STATS_RETENTION_DAYS", "120")))
STATS_TIMEZONE = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Barnaul"))


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def save_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def clean_username(value: object) -> str:
    return str(value or "").strip().lstrip("@")


def load_partner_catalog() -> dict[str, Any]:
    catalog = load_json(
        PARTNERS_CATALOG_PATH,
        {"version": 1, "entities": [], "collectors": [], "excluded": []},
    )
    catalog.setdefault("entities", [])
    catalog.setdefault("collectors", [])
    catalog.setdefault("excluded", [])
    return catalog


def flatten_partner_channels(catalog: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    catalog = catalog or load_partner_catalog()
    result: dict[str, dict[str, Any]] = {}

    for entity in catalog.get("entities", []):
        if not isinstance(entity, dict):
            continue
        entity_name = str(entity.get("name") or "").strip()
        relation = str(entity.get("relationship") or "unverified").strip()
        verification = str(entity.get("verification") or "").strip()
        for channel in entity.get("channels", []):
            if not isinstance(channel, dict):
                continue
            username = clean_username(channel.get("username"))
            if not username:
                continue
            result[username.casefold()] = {
                "username": username,
                "entity": entity_name,
                "relationship": relation,
                "verification": verification,
                "channel_type": str(channel.get("type") or "main"),
                "scan_mode": str(channel.get("scan_mode") or "nightly"),
                "notes": str(channel.get("notes") or ""),
            }

    for collector in catalog.get("collectors", []):
        if isinstance(collector, str):
            username = clean_username(collector)
            item: dict[str, Any] = {}
        elif isinstance(collector, dict):
            username = clean_username(collector.get("username"))
            item = collector
        else:
            continue
        if not username:
            continue
        result[username.casefold()] = {
            "username": username,
            "entity": str(item.get("name") or "Сборщик ссылок"),
            "relationship": "collector",
            "verification": str(item.get("verification") or "manual_exception"),
            "channel_type": "collector",
            "scan_mode": str(item.get("scan_mode") or "fast"),
            "notes": str(item.get("notes") or "Публичный чат/канал со ссылками разных авторов"),
        }

    for excluded in catalog.get("excluded", []):
        if not isinstance(excluded, dict):
            continue
        for raw in excluded.get("channels", []):
            username = clean_username(raw)
            if not username:
                continue
            result[username.casefold()] = {
                "username": username,
                "entity": str(excluded.get("name") or username),
                "relationship": "excluded",
                "verification": str(excluded.get("verification") or "manual"),
                "channel_type": "excluded",
                "scan_mode": "disabled",
                "notes": str(excluded.get("reason") or "Исключён после проверки"),
            }
    return result


def operational_sources(values: list[str], mode: str) -> list[str]:
    metadata = flatten_partner_channels()
    result: list[str] = []
    seen: set[str] = set()

    for raw in values:
        username = clean_username(raw)
        if not username:
            continue
        key = username.casefold()
        info = metadata.get(key)
        if info and info.get("scan_mode") == "disabled":
            continue
        # A catalogued channel belongs to exactly one operational list.
        # This also cleans stale promotions left in the opposite text file.
        configured_mode = str(info.get("scan_mode") or "") if info else ""
        if configured_mode in {"fast", "nightly"} and configured_mode != mode:
            continue
        if key not in seen:
            seen.add(key)
            result.append(username)

    for info in metadata.values():
        if info.get("scan_mode") != mode:
            continue
        username = clean_username(info.get("username"))
        if username and username.casefold() not in seen:
            seen.add(username.casefold())
            result.append(username)
    return result


def source_label(username: str) -> str:
    info = flatten_partner_channels().get(username.casefold())
    if not info:
        return f"@{username}"
    entity = str(info.get("entity") or "").strip()
    return f"@{username}" + (f" ({entity})" if entity and entity != username else "")


def _read_source_values(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    result: list[str] = []
    for line in lines:
        value = clean_username(line.split("#", 1)[0])
        if value:
            result.append(value)
    return result


def configured_source_keys() -> set[str]:
    """Return every currently operational fast/nightly source."""
    configured = operational_sources(_read_source_values(PUBLIC_SOURCES_PATH), "fast")
    configured += operational_sources(_read_source_values(NIGHTLY_SOURCES_PATH), "nightly")
    return {clean_username(value).casefold() for value in configured if clean_username(value)}


def _prune_source_mapping(value: object, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        username: entry
        for username, entry in value.items()
        if clean_username(username).casefold() in allowed
    }


def prune_unconfigured_runtime_sources(data: dict[str, Any]) -> None:
    """Remove stale source records after a channel is removed from both bases."""
    allowed = configured_source_keys()
    if not allowed:
        return
    if "sources" in data:
        data["sources"] = _prune_source_mapping(data.get("sources"), allowed)

    daily = data.get("daily")
    if isinstance(daily, dict):
        for entry in daily.values():
            if not isinstance(entry, dict):
                continue
            source_rows = _prune_source_mapping(entry.get("sources"), allowed)
            entry["sources"] = source_rows
            totals: dict[str, int] = {}
            for row in source_rows.values():
                if not isinstance(row, dict):
                    continue
                for key, raw in row.items():
                    if isinstance(raw, int) and not isinstance(raw, bool):
                        totals[key] = totals.get(key, 0) + raw
            entry["totals"] = totals

    samples = data.get("samples")
    if isinstance(samples, list):
        data["samples"] = [
            item
            for item in samples
            if not isinstance(item, dict)
            or clean_username(item.get("source")).casefold() in allowed
        ]


def load_health() -> dict[str, Any]:
    data = load_json(SOURCE_HEALTH_PATH, {"version": 1, "sources": {}})
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    prune_unconfigured_runtime_sources(data)
    return data


def save_health(data: dict[str, Any]) -> None:
    prune_unconfigured_runtime_sources(data)
    save_json(SOURCE_HEALTH_PATH, data)


def _health_entry(data: dict[str, Any], username: str) -> dict[str, Any]:
    sources = data.setdefault("sources", {})
    entry = sources.setdefault(username, {})
    entry.setdefault("checks", 0)
    entry.setdefault("successful_checks", 0)
    entry.setdefault("consecutive_errors", 0)
    entry.setdefault("consecutive_empty", 0)
    entry.setdefault("status", "unknown")
    return entry


def source_due_for_check(data: dict[str, Any], username: str, at: datetime | None = None) -> bool:
    at = at or now_utc()
    entry = data.get("sources", {}).get(username, {})
    if not isinstance(entry, dict) or entry.get("status") != "quarantined":
        return True
    next_check = parse_datetime(entry.get("next_recheck_at"))
    return next_check is None or at >= next_check


def record_source_success(
    data: dict[str, Any], username: str, messages_count: int, at: datetime | None = None
) -> None:
    at = at or now_utc()
    entry = _health_entry(data, username)
    entry["checks"] = int(entry.get("checks", 0)) + 1
    entry["successful_checks"] = int(entry.get("successful_checks", 0)) + 1
    entry["status"] = "ok"
    entry["last_success_at"] = at.isoformat()
    entry["last_checked_at"] = at.isoformat()
    entry["last_messages_count"] = int(messages_count)
    entry["consecutive_errors"] = 0
    entry["consecutive_empty"] = 0
    entry.pop("first_unavailable_at", None)
    entry.pop("last_error", None)
    entry.pop("quarantined_at", None)
    entry.pop("next_recheck_at", None)


def record_source_problem(
    data: dict[str, Any],
    username: str,
    kind: str,
    error: str = "",
    at: datetime | None = None,
) -> bool:
    """Record empty/error and return True if the source is quarantined."""
    at = at or now_utc()
    entry = _health_entry(data, username)
    entry["checks"] = int(entry.get("checks", 0)) + 1
    entry["last_checked_at"] = at.isoformat()
    entry["last_problem_at"] = at.isoformat()
    entry.setdefault("first_unavailable_at", at.isoformat())
    if error:
        entry["last_error"] = error[:500]

    if kind == "empty":
        entry["consecutive_empty"] = int(entry.get("consecutive_empty", 0)) + 1
        entry["consecutive_errors"] = 0
        threshold = QUARANTINE_EMPTY_THRESHOLD
    else:
        entry["consecutive_errors"] = int(entry.get("consecutive_errors", 0)) + 1
        entry["consecutive_empty"] = 0
        threshold = QUARANTINE_FAILURE_THRESHOLD

    current_count = max(
        int(entry.get("consecutive_errors", 0)),
        int(entry.get("consecutive_empty", 0)),
    )
    if current_count >= threshold:
        entry["status"] = "quarantined"
        entry.setdefault("quarantined_at", at.isoformat())
        entry["next_recheck_at"] = (
            at + timedelta(hours=QUARANTINE_RECHECK_HOURS)
        ).isoformat()
        return True

    entry["status"] = kind
    return False


def unavailable_days(entry: dict[str, Any], at: datetime | None = None) -> int:
    at = at or now_utc()
    first = parse_datetime(entry.get("first_unavailable_at"))
    if not first:
        return 0
    return max(0, (at.date() - first.astimezone(UTC).date()).days + 1)


def unavailable_sources(
    data: dict[str, Any], minimum_days: int | None = None
) -> list[tuple[str, dict[str, Any], int]]:
    minimum_days = minimum_days or UNAVAILABLE_REPORT_DAYS
    result: list[tuple[str, dict[str, Any], int]] = []
    for username, entry in data.get("sources", {}).items():
        if not isinstance(entry, dict) or entry.get("status") == "ok":
            continue
        days = unavailable_days(entry)
        if days >= minimum_days:
            result.append((username, entry, days))
    return sorted(result, key=lambda item: (-item[2], item[0].casefold()))


def load_stats() -> dict[str, Any]:
    data = load_json(SOURCE_STATS_PATH, {"version": 1, "sources": {}, "daily": {}})
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    data.setdefault("daily", {})
    prune_unconfigured_runtime_sources(data)
    return data


def _counter_container(data: dict[str, Any], source: str, day: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source_entry = data.setdefault("sources", {}).setdefault(source, {})
    daily_entry = data.setdefault("daily", {}).setdefault(day, {})
    source_day = daily_entry.setdefault("sources", {}).setdefault(source, {})
    daily_entry.setdefault("totals", {})
    return source_entry, source_day


def increment_stat(
    data: dict[str, Any],
    source: str,
    name: str,
    amount: int = 1,
    at: datetime | None = None,
) -> None:
    at = at or now_utc()
    day = at.astimezone(STATS_TIMEZONE).date().isoformat()
    source_entry, source_day = _counter_container(data, source, day)
    source_entry[name] = int(source_entry.get(name, 0)) + amount
    source_day[name] = int(source_day.get(name, 0)) + amount
    totals = data["daily"][day]["totals"]
    totals[name] = int(totals.get(name, 0)) + amount
    source_entry["last_updated_at"] = at.isoformat()


def set_stat_timestamp(
    data: dict[str, Any], source: str, name: str, value: datetime | None = None
) -> None:
    value = value or now_utc()
    entry = data.setdefault("sources", {}).setdefault(source, {})
    entry[name] = value.isoformat()


def record_source_check_stats(
    data: dict[str, Any], source: str, status: str, messages_count: int = 0
) -> None:
    increment_stat(data, source, "checks")
    if status == "ok":
        increment_stat(data, source, "successful_checks")
        increment_stat(data, source, "messages_scanned", max(0, messages_count))
    elif status == "empty":
        increment_stat(data, source, "empty_checks")
    elif status == "quarantined_skip":
        increment_stat(data, source, "quarantine_skips")
    else:
        increment_stat(data, source, "errors")


def mark_unique_wheel_post(
    data: dict[str, Any], source: str, post_key: str, wheel_key: str
) -> bool:
    entry = data.setdefault("sources", {}).setdefault(source, {})
    recent = entry.setdefault("recent_post_keys", {})
    if post_key in recent:
        return False
    timestamp = now_utc().isoformat()
    recent[post_key] = {"wheel": wheel_key, "seen_at": timestamp}
    increment_stat(data, source, "wheel_posts")
    set_stat_timestamp(data, source, "last_wheel_post_at")
    if len(recent) > 400:
        ordered = sorted(
            recent.items(),
            key=lambda item: str(item[1].get("seen_at", "")),
            reverse=True,
        )[:300]
        entry["recent_post_keys"] = dict(ordered)
    return True


def prune_stats(data: dict[str, Any], at: datetime | None = None) -> None:
    at = at or now_utc()
    cutoff = (at - timedelta(days=STATS_RETENTION_DAYS)).date().isoformat()
    data["daily"] = {
        day: value for day, value in data.get("daily", {}).items() if day >= cutoff
    }


def save_stats(data: dict[str, Any]) -> None:
    prune_unconfigured_runtime_sources(data)
    prune_stats(data)
    save_json(SOURCE_STATS_PATH, data)


def top_sources(data: dict[str, Any], limit: int = 5) -> list[tuple[str, int, dict[str, Any]]]:
    ranked: list[tuple[str, int, dict[str, Any]]] = []
    for source, entry in data.get("sources", {}).items():
        if not isinstance(entry, dict):
            continue
        score = (
            int(entry.get("activation_sent", 0)) * 4
            + int(entry.get("preliminary_sent", 0)) * 2
            + int(entry.get("wheel_posts", 0))
        )
        if score:
            ranked.append((source, score, entry))
    return sorted(ranked, key=lambda item: (-item[1], item[0].casefold()))[:limit]


def _sanitize_excerpt(value: str, limit: int = 900) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]


def load_unknown_samples() -> dict[str, Any]:
    data = load_json(UNKNOWN_TIMER_PATH, {"version": 1, "samples": []})
    data.setdefault("version", 1)
    data.setdefault("samples", [])
    prune_unconfigured_runtime_sources(data)
    return data


def record_unknown_timer_sample(
    data: dict[str, Any],
    *,
    source: str,
    message_id: int,
    message_url: str,
    wheel_url: str,
    wheel_identifier: str,
    status: str,
    method: str,
    telegram_text: str,
    page_excerpt: str,
    reason: str = "parser_unknown",
) -> bool:
    excerpt = _sanitize_excerpt(page_excerpt)
    telegram_excerpt = _sanitize_excerpt(telegram_text, 600)
    raw = "|".join(
        [source.casefold(), str(message_id), wheel_identifier.casefold(), status, method, excerpt]
    )
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    samples = data.setdefault("samples", [])
    if any(isinstance(item, dict) and item.get("fingerprint") == fingerprint for item in samples):
        return False
    samples.append(
        {
            "fingerprint": fingerprint,
            "captured_at": now_utc().isoformat(),
            "reason": reason,
            "source": source,
            "message_id": message_id,
            "message_url": message_url,
            "wheel_url": wheel_url,
            "wheel_identifier": wheel_identifier,
            "status": status,
            "method": method,
            "telegram_excerpt": telegram_excerpt,
            "page_excerpt": excerpt,
        }
    )
    if len(samples) > UNKNOWN_TIMER_LIMIT:
        data["samples"] = samples[-UNKNOWN_TIMER_LIMIT:]
    return True


def save_unknown_samples(data: dict[str, Any]) -> None:
    prune_unconfigured_runtime_sources(data)
    save_json(UNKNOWN_TIMER_PATH, data)
