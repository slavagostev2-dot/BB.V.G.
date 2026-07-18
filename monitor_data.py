from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
PARTNERS_CATALOG_PATH = ROOT / "partners_catalog.json"
SOURCE_HEALTH_PATH = ROOT / "source_health.json"
SOURCE_STATS_PATH = ROOT / "source_stats.json"
UNKNOWN_TIMER_PATH = ROOT / "unknown_timer_samples.json"
PUBLIC_SOURCES_PATH = ROOT / "public_sources.txt"
NIGHTLY_SOURCES_PATH = ROOT / "source_catalog.txt"

# Every tracked JSON file has one declared owner and one operational role.  The
# contract deliberately includes frozen Mini App files and package.json so a
# newly added state file cannot silently bypass the ownership review.
JSON_STATE_CONTRACTS: dict[str, dict[str, Any]] = {
    "activation_runtime_state.json": {"category": "archive", "owner": "frozen-miniapp"},
    "admin_action_queue.json": {
        "category": "authoritative",
        "owner": "admin-action-cas",
        "schema": ("format", ("bbvg-admin-action-queue-v1",)),
    },
    "admin_panel_status.json": {
        "category": "diagnostic",
        "owner": "control-center-status",
        "schema": ("heartbeat_version", (1,)),
    },
    "bot_access.json": {
        "category": "compatibility",
        "owner": "encrypted-private-state",
        "schema": ("version", (3,)),
    },
    "bot_private_state.enc.json": {
        "category": "authoritative",
        "owner": "encrypted-private-state-cas",
        "schema": ("format", ("bbvg-bot-state-v2",)),
    },
    "candidate_moderation.json": {
        "category": "authoritative",
        "owner": "control-center-moderation",
        "schema": ("version", (1,)),
    },
    "discovery_state.json": {
        "category": "authoritative",
        "owner": "nightly-discovery",
        "schema": ("version", (1, 2)),
    },
    "identifier_sources.json": {
        "category": "config",
        "owner": "source-catalog",
        "schema": ("version", (1,)),
    },
    "incident_state.json": {
        "category": "diagnostic",
        "owner": "system-health",
        "schema": ("version", (1,)),
    },
    "intelligence_state.json": {
        "category": "authoritative",
        "owner": "source-intelligence",
        "schema": ("version", (1,)),
    },
    "miniapp_deploy_runtime.json": {"category": "archive", "owner": "frozen-miniapp"},
    "miniapp_deployment.json": {"category": "archive", "owner": "frozen-miniapp"},
    "monitor_recovery_status.json": {
        "category": "diagnostic",
        "owner": "monitor-recovery",
        "schema": ("version", (41,)),
    },
    "monitor_status.json": {
        "category": "diagnostic",
        "owner": "monitor",
        "schema": ("version", (3,)),
    },
    "notification_delivery_state.json": {
        "category": "authoritative",
        "owner": "notification-ledger",
        "schema": (
            "format",
            ("bbvg-notification-delivery-v2", "bbvg-notification-delivery-v3"),
        ),
    },
    "partners_catalog.json": {
        "category": "config",
        "owner": "source-catalog",
        "schema": ("version", (2,)),
    },
    "private_state_deployment.json": {"category": "archive", "owner": "frozen-miniapp"},
    "source_health.json": {
        "category": "authoritative",
        "owner": "monitor",
        "schema": ("version", (1,)),
    },
    "source_registry.json": {
        "category": "cache",
        "owner": "source-registry",
        "schema": ("version", (2,)),
    },
    "source_requests.json": {
        "category": "compatibility",
        "owner": "encrypted-private-state",
        "schema": ("version", (1,)),
    },
    "source_stats.json": {
        "category": "authoritative",
        "owner": "monitor",
        "schema": ("version", (1,)),
    },
    "source_tier_state.json": {
        "category": "diagnostic",
        "owner": "source-tier-maintenance",
        "schema": ("version", (1,)),
    },
    "source_transport_state.json": {
        "category": "diagnostic",
        "owner": "source-transport",
        "schema": ("version", (1,)),
    },
    "state.json": {
        "category": "authoritative",
        "owner": "monitor",
        "schema": ("version", (6,)),
    },
    "state_api/package.json": {"category": "archive", "owner": "frozen-miniapp"},
    "state_api_runtime.json": {"category": "archive", "owner": "frozen-miniapp"},
    "system_check_state.json": {
        "category": "diagnostic",
        "owner": "system-health",
        "schema": ("version", (1,)),
    },
    "unknown_timer_samples.json": {
        "category": "authoritative",
        "owner": "monitor",
        "schema": ("version", (1,)),
    },
}

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
SOURCE_RATING_EPOCH_DAY = os.getenv("SOURCE_RATING_EPOCH_DAY", "2026-07-17")
WHEEL_POST_RECOVERY_VERSION = 1
SOURCE_RATING_RESET_FIELDS = (
    "wheel_posts",
    "admin_confirmed_wheels",
    "admin_rejected_wheels",
    "quality_score",
    "quality_decisions",
    "activation_sent",
)


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


def atomic_write_text(path: Path, text: str) -> None:
    """Durably replace one file without exposing a truncated destination."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def save_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_json(path, value)


def validate_json_state_contracts(root: Path = ROOT) -> list[str]:
    """Return ownership/schema violations for the complete tracked inventory."""

    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.json"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if tracked.returncode == 0:
        actual = {name for name in tracked.stdout.splitlines() if name}
    else:
        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*.json")
            if ".git" not in path.parts
        }
    expected = set(JSON_STATE_CONTRACTS)
    errors = [f"unowned:{name}" for name in sorted(actual - expected)]
    errors.extend(f"missing:{name}" for name in sorted(expected - actual))
    for name, contract in JSON_STATE_CONTRACTS.items():
        schema = contract.get("schema")
        path = root / name
        if not schema or not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"invalid:{name}:{type(exc).__name__}")
            continue
        field, accepted = schema
        if not isinstance(value, dict) or value.get(field) not in accepted:
            errors.append(f"schema:{name}:{field}")
    return errors


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
    """Return enabled sources from the requested operational text list.

    The two text files are the live tier assignment. Catalog metadata describes
    a source, but must not pin it to a tier: otherwise an automatic seven-day
    move would be silently undone on the next monitor iteration.
    """
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
        if key not in seen:
            seen.add(key)
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
    entry.pop("last_transport_error", None)
    entry.pop("last_transport_outage_at", None)
    entry.pop("failure_code", None)
    entry.pop("failure_reason", None)
    entry.pop("quarantined_at", None)
    entry.pop("next_recheck_at", None)


def classify_source_problem(kind: str, error: str = "") -> tuple[str, str]:
    """Return a stable machine code and a short Russian explanation."""

    text = str(error or "").casefold()
    if kind == "empty":
        return "empty_public_feed", "публичная страница открылась, но сообщений не найдено"
    if "404" in text or "not found" in text:
        return "removed_or_renamed", "канал удалён, переименован или username больше не существует"
    if "401" in text or "403" in text or "private" in text or "forbidden" in text:
        return "private_or_restricted", "канал закрыт или ограничил публичный доступ"
    if "451" in text or "blocked" in text:
        return "access_blocked", "доступ к странице ограничен на стороне Telegram или сети"
    if "429" in text or "too many requests" in text:
        return "rate_limited", "Telegram временно ограничил частоту запросов"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "tls_error", "ошибка TLS-сертификата при подключении к telegram.me"
    if "resolve" in text or "name or service not known" in text or "dns" in text:
        return "dns_error", "не удалось определить адрес telegram.me через DNS"
    if "timeout" in text or "timed out" in text:
        return "timeout", "telegram.me не ответил за отведённое время"
    if "proxy" in text or "connection" in text or "network" in text:
        return "network_error", "сетевая ошибка при подключении к telegram.me"
    if "5" in text and any(code in text for code in ("500", "502", "503", "504")):
        return "telegram_server_error", "временный сбой сервера Telegram"
    return "unknown_error", "неизвестная ошибка проверки источника"


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
    failure_code, failure_reason = classify_source_problem(kind, error)
    entry["failure_code"] = failure_code
    entry["failure_reason"] = failure_reason

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


def apply_source_rating_epoch(data: dict[str, Any]) -> bool:
    """Reset public source wheel/rating counters once at the configured epoch."""
    if data.get("source_rating_epoch_day") == SOURCE_RATING_EPOCH_DAY:
        return False
    data.pop("admin_wheel_decisions", None)
    for entry in data.setdefault("sources", {}).values():
        if not isinstance(entry, dict):
            continue
        for field in SOURCE_RATING_RESET_FIELDS:
            entry.pop(field, None)
    for daily_entry in data.setdefault("daily", {}).values():
        if not isinstance(daily_entry, dict):
            continue
        totals = daily_entry.setdefault("totals", {})
        for field in SOURCE_RATING_RESET_FIELDS:
            totals.pop(field, None)
        for source_entry in daily_entry.setdefault("sources", {}).values():
            if not isinstance(source_entry, dict):
                continue
            for field in SOURCE_RATING_RESET_FIELDS:
                source_entry.pop(field, None)
    data["source_rating_epoch_day"] = SOURCE_RATING_EPOCH_DAY
    data["source_rating_reset_at"] = f"{SOURCE_RATING_EPOCH_DAY}T00:00:00+07:00"
    return True


def recover_wheel_post_stats_from_recent_keys(data: dict[str, Any]) -> bool:
    """Restore wheel finding counters erased by the conflicting rating epochs."""

    if int(data.get("wheel_post_recovery_version", 0) or 0) >= WHEEL_POST_RECOVERY_VERSION:
        return False

    counting_from = parse_datetime(
        data.get("source_rating_counting_from") or data.get("source_rating_reset_at")
    )
    if counting_from is None:
        counting_from = datetime.fromisoformat(
            f"{SOURCE_RATING_EPOCH_DAY}T00:00:00+07:00"
        )

    source_counts: dict[str, int] = {}
    daily_counts: dict[str, dict[str, int]] = {}
    recovered_records = 0
    sources = data.setdefault("sources", {})
    for source, entry in sources.items():
        if not isinstance(entry, dict):
            continue
        recent = entry.get("recent_post_keys")
        if not isinstance(recent, dict):
            continue
        for raw in recent.values():
            if not isinstance(raw, dict):
                continue
            observed_at = parse_datetime(raw.get("seen_at"))
            if observed_at is None or observed_at < counting_from:
                continue
            day = observed_at.astimezone(STATS_TIMEZONE).date().isoformat()
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
            by_source = daily_counts.setdefault(day, {})
            by_source[str(source)] = by_source.get(str(source), 0) + 1
            recovered_records += 1

    for entry in sources.values():
        if isinstance(entry, dict):
            entry.pop("wheel_posts", None)
    for daily_entry in data.setdefault("daily", {}).values():
        if not isinstance(daily_entry, dict):
            continue
        totals = daily_entry.setdefault("totals", {})
        if isinstance(totals, dict):
            totals.pop("wheel_posts", None)
        for source_entry in daily_entry.setdefault("sources", {}).values():
            if isinstance(source_entry, dict):
                source_entry.pop("wheel_posts", None)

    for source, count in source_counts.items():
        sources.setdefault(source, {})["wheel_posts"] = count
    for day, rows in daily_counts.items():
        daily_entry = data.setdefault("daily", {}).setdefault(
            day, {"sources": {}, "totals": {}}
        )
        daily_entry.setdefault("totals", {})["wheel_posts"] = sum(rows.values())
        for source, count in rows.items():
            daily_entry.setdefault("sources", {}).setdefault(source, {})[
                "wheel_posts"
            ] = count

    data["wheel_post_recovery_version"] = WHEEL_POST_RECOVERY_VERSION
    data["wheel_post_recovered_at"] = now_utc().isoformat()
    data["wheel_post_recovered_records"] = recovered_records
    data["wheel_post_recovered_from"] = counting_from.isoformat()
    return True


def load_stats() -> dict[str, Any]:
    data = load_json(SOURCE_STATS_PATH, {"version": 1, "sources": {}, "daily": {}})
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    data.setdefault("daily", {})
    apply_source_rating_epoch(data)
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
    data: dict[str, Any],
    source: str,
    status: str,
    messages_count: int = 0,
    at: datetime | None = None,
) -> None:
    at = at or now_utc()
    source_entry = data.setdefault("sources", {}).setdefault(source, {})
    source_entry.setdefault("first_checked_at", at.isoformat())
    source_entry["last_checked_at"] = at.isoformat()
    increment_stat(data, source, "checks", at=at)
    if status == "ok":
        increment_stat(data, source, "successful_checks", at=at)
        increment_stat(
            data, source, "messages_scanned", max(0, messages_count), at=at
        )
    elif status == "empty":
        increment_stat(data, source, "empty_checks", at=at)
    elif status == "quarantined_skip":
        increment_stat(data, source, "quarantine_skips", at=at)
    else:
        increment_stat(data, source, "errors", at=at)


def sources_without_recent_wheels(
    data: dict[str, Any],
    sources: list[str],
    minimum_days: int = 7,
    at: datetime | None = None,
) -> list[tuple[str, dict[str, Any], int]]:
    at = at or now_utc()
    threshold = timedelta(days=max(1, minimum_days))
    result: list[tuple[str, dict[str, Any], int]] = []
    source_rows = data.get("sources", {})
    for source in sources:
        entry = source_rows.get(source, {}) if isinstance(source_rows, dict) else {}
        if not isinstance(entry, dict):
            continue
        first_checked = parse_datetime(entry.get("first_checked_at"))
        if first_checked is None:
            continue
        last_wheel = parse_datetime(entry.get("last_wheel_post_at"))
        reference = last_wheel or first_checked
        elapsed = at - reference
        if elapsed < threshold:
            continue
        days = max(minimum_days, int(elapsed.total_seconds() // 86400))
        result.append((source, entry, days))
    return sorted(result, key=lambda item: (-item[2], item[0].casefold()))


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
        score = int(entry.get("quality_score", 0) or 0)
        if score:
            ranked.append((source, score, entry))
    return sorted(ranked, key=lambda item: (-item[1], item[0].casefold()))[:limit]


def _stats_day(at: datetime) -> str:
    return at.astimezone(STATS_TIMEZONE).date().isoformat()


def record_admin_wheel_decision(
    data: dict[str, Any],
    *,
    wheel_key: str,
    sources: list[str],
    decision: str,
    actor: str = "admin",
    at: datetime | None = None,
) -> bool:
    """Persist one idempotent administrator verdict and rebuild rating fields."""
    normalized = str(wheel_key or "").strip().casefold()
    if not normalized or decision not in {"confirmed", "inactive"}:
        raise ValueError("invalid administrator wheel decision")
    at = at or now_utc()
    clean_sources = sorted(
        {clean_username(value) for value in sources if clean_username(value)},
        key=str.casefold,
    )
    decisions = data.setdefault("admin_wheel_decisions", {})
    previous = decisions.get(normalized)
    if not clean_sources and isinstance(previous, dict):
        clean_sources = sorted(
            {clean_username(value) for value in previous.get("sources", []) if clean_username(value)},
            key=str.casefold,
        )
    previous_decision = str(previous.get("decision") or "") if isinstance(previous, dict) else ""
    previous_sources = {
        clean_username(value).casefold()
        for value in (previous.get("sources", []) if isinstance(previous, dict) else [])
        if clean_username(value)
    }
    current_sources = {value.casefold() for value in clean_sources}
    changed = previous_decision != decision or previous_sources != current_sources
    if not changed:
        return False

    decisions[normalized] = {
        "decision": decision,
        "sources": clean_sources,
        "decided_at": at.isoformat(),
        "day": _stats_day(at),
        "actor": "admin",
    }

    # Remove only derived administrator fields. Automatic monitoring counters
    # remain untouched.
    for entry in data.setdefault("sources", {}).values():
        if not isinstance(entry, dict):
            continue
        entry.pop("admin_confirmed_wheels", None)
        entry.pop("admin_rejected_wheels", None)
        entry.pop("quality_score", None)
        entry.pop("quality_decisions", None)
    for daily_entry in data.setdefault("daily", {}).values():
        if not isinstance(daily_entry, dict):
            continue
        totals = daily_entry.setdefault("totals", {})
        totals.pop("admin_confirmed_wheels", None)
        totals.pop("admin_rejected_wheels", None)
        for entry in daily_entry.setdefault("sources", {}).values():
            if isinstance(entry, dict):
                entry.pop("admin_confirmed_wheels", None)
                entry.pop("admin_rejected_wheels", None)

    for decided_wheel, record in decisions.items():
        if not isinstance(record, dict):
            continue
        verdict = str(record.get("decision") or "")
        if verdict not in {"confirmed", "inactive"}:
            continue
        metric = "admin_confirmed_wheels" if verdict == "confirmed" else "admin_rejected_wheels"
        points = 40 if verdict == "confirmed" else -45
        day = str(record.get("day") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            decided_at = parse_datetime(record.get("decided_at")) or at
            day = _stats_day(decided_at)
            record["day"] = day
        daily_entry = data["daily"].setdefault(day, {"sources": {}, "totals": {}})
        daily_entry.setdefault("totals", {})[metric] = int(
            daily_entry["totals"].get(metric, 0)
        ) + 1
        for source in record.get("sources", []):
            source_name = clean_username(source)
            if not source_name:
                continue
            source_entry = data["sources"].setdefault(source_name, {})
            source_entry[metric] = int(source_entry.get(metric, 0)) + 1
            source_entry["quality_decisions"] = source_entry.get("quality_decisions", {})
            source_entry["quality_decisions"][decided_wheel] = points
            source_entry["quality_score"] = sum(
                int(value or 0) for value in source_entry["quality_decisions"].values()
            )
            source_entry["last_updated_at"] = at.isoformat()
            source_day = daily_entry.setdefault("sources", {}).setdefault(source_name, {})
            source_day[metric] = int(source_day.get(metric, 0)) + 1
    return True


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
