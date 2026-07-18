from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import monitor_data as data_store

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "incident_state.json"
UTC = timezone.utc
RESOLVED_RETENTION_DAYS = 30
REOPEN_COOLDOWN_HOURS = max(1, int(os.getenv("INCIDENT_REOPEN_COOLDOWN_HOURS", "6")))


def now_utc() -> datetime:
    return datetime.now(UTC)


def load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("version", 1)
    value.setdefault("sequence", 0)
    value.setdefault("incidents", {})
    return value


def save_state(value: dict[str, Any]) -> None:
    cutoff = now_utc() - timedelta(days=RESOLVED_RETENTION_DAYS)
    incidents = value.get("incidents") if isinstance(value.get("incidents"), dict) else {}
    kept: dict[str, Any] = {}
    for key, entry in incidents.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "resolved":
            kept[key] = entry
            continue
        resolved_at = parse_datetime(entry.get("resolved_at"))
        if resolved_at is None or resolved_at >= cutoff:
            kept[key] = entry
    value["incidents"] = kept
    value["active_count"] = sum(
        1 for entry in kept.values() if isinstance(entry, dict) and entry.get("status") == "active"
    )
    value["updated_at"] = now_utc().isoformat()
    data_store.atomic_write_json(STATE_PATH, value)


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)


def incident_key(scope: str, kind: str, subject: str = "") -> str:
    normalized = "|".join(
        [str(scope).strip().casefold(), str(kind).strip().casefold(), str(subject).strip().casefold()]
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"{scope}:{kind}:{digest}"


def normalize_finding(value: dict[str, Any]) -> dict[str, Any]:
    scope = str(value.get("scope") or "system").strip()
    kind = str(value.get("kind") or "unknown").strip()
    subject = str(value.get("subject") or "").strip()
    return {
        "key": incident_key(scope, kind, subject),
        "scope": scope,
        "kind": kind,
        "subject": subject,
        "severity": str(value.get("severity") or "warning").strip(),
        "title": str(value.get("title") or kind).strip()[:240],
        "detail": str(value.get("detail") or "").strip()[:2000],
        "metadata": value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
    }


def reconcile(findings: Iterable[dict[str, Any]], *, scope: str) -> dict[str, Any]:
    state = load_state()
    incidents = state.setdefault("incidents", {})
    current_time = now_utc().isoformat()
    normalized = [normalize_finding({**finding, "scope": scope}) for finding in findings]
    current_keys = {finding["key"] for finding in normalized}
    changed = False

    for finding in normalized:
        key = finding["key"]
        previous = incidents.get(key)
        if not isinstance(previous, dict):
            previous = {}
        was_active = previous.get("status") == "active"
        entry = dict(previous)
        entry.update(finding)
        entry["status"] = "active"
        entry.setdefault("first_seen_at", current_time)
        entry["last_seen_at"] = current_time
        entry["occurrences"] = int(entry.get("occurrences", 0) or 0) + 1
        entry.pop("resolved_at", None)
        entry.pop("resolution_notification_pending", None)
        if not was_active:
            state["sequence"] = int(state.get("sequence", 0) or 0) + 1
            entry["opened_sequence"] = state["sequence"]
            previous_notice = parse_datetime(previous.get("open_notified_at"))
            reopened_too_soon = bool(
                previous.get("status") == "resolved"
                and previous_notice is not None
                and now_utc() - previous_notice < timedelta(hours=REOPEN_COOLDOWN_HOURS)
            )
            entry["open_notification_pending"] = not reopened_too_soon
            if reopened_too_soon:
                entry["reopen_notification_suppressed_at"] = current_time
                entry["reopen_notification_suppressed_until"] = (
                    previous_notice + timedelta(hours=REOPEN_COOLDOWN_HOURS)
                ).isoformat()
            entry.pop("open_notified_at", None)
            changed = True
        elif any(previous.get(name) != entry.get(name) for name in ("severity", "title", "detail", "metadata")):
            changed = True
        incidents[key] = entry

    for key, entry in list(incidents.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("scope") != scope or entry.get("status") != "active" or key in current_keys:
            continue
        entry["status"] = "resolved"
        entry["resolved_at"] = current_time
        entry["last_seen_at"] = current_time
        state["sequence"] = int(state.get("sequence", 0) or 0) + 1
        entry["resolved_sequence"] = state["sequence"]
        if entry.get("open_notified_at"):
            entry["resolution_notification_pending"] = True
        else:
            entry["open_notification_pending"] = False
            entry["resolution_notification_pending"] = False
            entry["notification_skipped_because_resolved_before_delivery"] = current_time
        changed = True

    active = [
        entry for entry in incidents.values()
        if isinstance(entry, dict) and entry.get("status") == "active"
    ]
    state["active_count"] = len(active)
    state["last_reconcile_scope"] = scope
    state["last_reconcile_at"] = current_time
    if changed:
        state["last_change_at"] = current_time
    state["last_summary"] = {
        "scope": scope,
        "active": len(active),
        "new_notification_count": len(pending_open(state)),
        "recovery_notification_count": len(pending_resolved(state)),
    }
    save_state(state)
    return state


def pending_open(state: dict[str, Any]) -> list[dict[str, Any]]:
    incidents = state.get("incidents") if isinstance(state.get("incidents"), dict) else {}
    result = [
        entry for entry in incidents.values()
        if isinstance(entry, dict)
        and entry.get("status") == "active"
        and bool(entry.get("open_notification_pending"))
        and not entry.get("open_notified_at")
    ]
    return sorted(result, key=lambda entry: int(entry.get("opened_sequence", 0) or 0))


def pending_resolved(state: dict[str, Any]) -> list[dict[str, Any]]:
    incidents = state.get("incidents") if isinstance(state.get("incidents"), dict) else {}
    result = [
        entry for entry in incidents.values()
        if isinstance(entry, dict)
        and entry.get("status") == "resolved"
        and bool(entry.get("resolution_notification_pending"))
        and not entry.get("resolution_notified_at")
    ]
    return sorted(result, key=lambda entry: int(entry.get("resolved_sequence", 0) or 0))


def mark_notified(keys: Iterable[str], phase: str) -> dict[str, Any]:
    state = load_state()
    incidents = state.get("incidents") if isinstance(state.get("incidents"), dict) else {}
    timestamp = now_utc().isoformat()
    for key in keys:
        entry = incidents.get(key)
        if not isinstance(entry, dict):
            continue
        if phase == "open":
            entry["open_notified_at"] = timestamp
            entry["open_notification_pending"] = False
        elif phase == "resolved":
            entry["resolution_notified_at"] = timestamp
            entry["resolution_notification_pending"] = False
    save_state(state)
    return state


def format_open_message(entries: list[dict[str, Any]]) -> str:
    lines = ["⚠️ <b>BB V.G.: обнаружен сбой</b>", ""]
    for index, entry in enumerate(entries[:12], 1):
        lines.append(f"{index}. <b>{entry.get('title', 'Сбой')}</b>")
        detail = str(entry.get("detail") or "").strip()
        if detail:
            lines.append(detail[:600])
    if len(entries) > 12:
        lines.append(f"Ещё событий: {len(entries) - 12}")
    lines.extend(["", "Повторные сообщения по тем же сбоям подавляются до восстановления."])
    return "\n".join(lines)[:4000]


def format_resolved_message(entries: list[dict[str, Any]]) -> str:
    lines = ["✅ <b>BB V.G.: работа восстановлена</b>", ""]
    for index, entry in enumerate(entries[:12], 1):
        lines.append(f"{index}. {entry.get('title', 'Сбой устранён')}")
    if len(entries) > 12:
        lines.append(f"Ещё восстановлений: {len(entries) - 12}")
    return "\n".join(lines)[:4000]


def format_digest_message(
    opened: list[dict[str, Any]], resolved: list[dict[str, Any]]
) -> str:
    lines = ["🛠 <b>BB V.G.: единая сводка диагностики</b>", ""]
    if opened:
        lines.append("<b>Новые сбои</b>")
        for index, entry in enumerate(opened[:10], 1):
            lines.append(f"{index}. <b>{entry.get('title', 'Сбой')}</b>")
            detail = str(entry.get("detail") or "").strip()
            if detail:
                lines.append(detail[:420])
    if resolved:
        if opened:
            lines.append("")
        lines.append("<b>Восстановлено</b>")
        for index, entry in enumerate(resolved[:10], 1):
            lines.append(f"{index}. {entry.get('title', 'Сбой устранён')}")
    lines.extend([
        "",
        "Повторы подавляются: по одному сообщению на инцидент до восстановления.",
    ])
    return "\n".join(lines)[:4000]


def self_test() -> None:
    assert incident_key("system", "dns", "telegram.me") == incident_key("system", "dns", "TELEGRAM.ME")
    finding = normalize_finding({"scope": "test", "kind": "dns", "title": "DNS"})
    assert finding["key"].startswith("test:dns:")
    assert "единая сводка" in format_digest_message([finding], []).casefold()
    print("incident_manager self-test passed")


if __name__ == "__main__":
    self_test()
