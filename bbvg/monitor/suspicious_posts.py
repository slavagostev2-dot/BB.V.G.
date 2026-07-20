from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from bbvg.ai_core import AIClient, FEATURE_SUSPICIOUS_POST_ANALYSIS, client_from_env

UTC = timezone.utc
ALLOWED = frozenset({
    "active_wheel",
    "possible_wheel_announcement",
    "old_wheel_reference",
    "betboom_promotion",
    "irrelevant",
    "uncertain",
})
ALERTING = frozenset({"active_wheel", "possible_wheel_announcement"})
WHEEL_WORDS = re.compile(r"\b(?:колес\w*|крутил\w*|крутим\w*|прокрут\w*|спин\w*|wheel\w*|spin\w*)\b", re.I)
BRAND_WORDS = re.compile(r"\b(?:betboom|bet\s*boom|бетбум|бэтбум|bb)\b", re.I)
TIME_WORDS = re.compile(r"\b(?:скоро|сегодня|завтра|через|старт\w*|ссылк\w*|позже|стрим\w*)\b", re.I)
DRAW_WORDS = re.compile(r"\b(?:розыгрыш\w*|приз\w*|участв\w*)\b", re.I)
DIRECT_LINK = re.compile(r"(?:https?://)?(?:www\.)?betboom\.ru/freestream/[A-Za-z0-9._~-]+", re.I)

_RUNTIME_STATE: dict[str, Any] | None = None


@dataclass(frozen=True)
class SuspiciousPost:
    source: str
    message_id: int
    date: datetime
    text: str
    message_url: str


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


def candidate_score(text: str) -> int:
    value = str(text or "")
    if not value or DIRECT_LINK.search(value):
        return 0
    return (
        (3 if WHEEL_WORDS.search(value) else 0)
        + (2 if BRAND_WORDS.search(value) else 0)
        + (1 if TIME_WORDS.search(value) else 0)
        + (1 if DRAW_WORDS.search(value) else 0)
    )


def is_candidate(text: str) -> bool:
    value = str(text or "").strip()
    return len(value) >= 8 and candidate_score(value) >= 3


def _key(post: SuspiciousPost) -> str:
    raw = f"{post.source.casefold()}:{post.message_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _normalize(data: dict[str, Any] | None) -> tuple[str, float, str]:
    value = data if isinstance(data, dict) else {}
    classification = str(value.get("classification") or "uncertain").strip().casefold()
    if classification not in ALLOWED:
        classification = "uncertain"
    try:
        confidence = float(value.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(value.get("reason") or "").strip()[:500]
    return classification, confidence, reason


def ensure_state(stats: dict[str, Any]) -> dict[str, Any]:
    value = stats.setdefault("ai_post_analysis", {})
    if not isinstance(value, dict):
        value = {}
        stats["ai_post_analysis"] = value
    value.setdefault("version", 1)
    value.setdefault("posts", {})
    value.setdefault("last_summary", {})
    return value


def _prune(records: dict[str, Any], current: datetime) -> bool:
    changed = False
    cutoff = current - timedelta(days=_int_env("AI_SUSPICIOUS_POST_RETENTION_DAYS", 14, 1, 90))
    for key in list(records):
        row = records.get(key)
        if not isinstance(row, dict):
            records.pop(key, None)
            changed = True
            continue
        analyzed = _parse_time(row.get("analyzed_at"))
        if analyzed is not None and analyzed < cutoff:
            records.pop(key, None)
            changed = True
    limit = _int_env("AI_SUSPICIOUS_POST_MAX_RECORDS", 1000, 100, 5000)
    if len(records) > limit:
        ordered = sorted(records, key=lambda key: str(records[key].get("analyzed_at", "")))
        for key in ordered[: len(records) - limit]:
            records.pop(key, None)
            changed = True
    return changed


def _alert_from_record(key: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_key": key,
        "source": str(row.get("source") or "unknown"),
        "message_url": str(row.get("message_url") or ""),
        "classification": str(row.get("classification") or "uncertain"),
        "confidence": float(row.get("confidence", 0.0) or 0.0),
        "reason": str(row.get("reason") or "")[:500],
    }


def analyze_posts(
    posts: Iterable[SuspiciousPost],
    state: dict[str, Any],
    *,
    client: AIClient | None = None,
    current: datetime | None = None,
) -> dict[str, Any]:
    ai = client or client_from_env()
    summary: dict[str, Any] = {
        "status": "disabled",
        "candidates": 0,
        "analyzed": 0,
        "alerts": [],
        "provider_failures": 0,
        "skipped_seen": 0,
        "changed": False,
    }
    if not ai.feature_enabled(FEATURE_SUSPICIOUS_POST_ANALYSIS):
        return summary
    if not ai.status_snapshot().get("provider_configured"):
        summary["status"] = "not_configured"
        return summary

    summary["status"] = "enabled"
    now = (current or datetime.now(UTC)).astimezone(UTC)
    max_age = timedelta(minutes=_int_env("AI_SUSPICIOUS_POST_MAX_AGE_MINUTES", 30, 5, 180))
    threshold = _float_env("AI_SUSPICIOUS_POST_MIN_CONFIDENCE", 0.80, 0.50, 0.99)
    active_threshold = max(
        threshold,
        _float_env("AI_SUSPICIOUS_ACTIVE_MIN_CONFIDENCE", 0.88, 0.50, 0.99),
    )

    records = state.setdefault("posts", {})
    if not isinstance(records, dict):
        records = {}
        state["posts"] = records
        summary["changed"] = True
    if _prune(records, now):
        summary["changed"] = True

    for post in posts:
        if not is_candidate(post.text):
            continue
        published = post.date.astimezone(UTC) if post.date.tzinfo else post.date.replace(tzinfo=UTC)
        if max(timedelta(0), now - published) > max_age:
            continue
        summary["candidates"] += 1
        key = _key(post)
        text_sha = _text_hash(post.text)
        previous = records.get(key)
        previous = previous if isinstance(previous, dict) else {}

        if previous.get("text_sha256") == text_sha:
            summary["skipped_seen"] += 1
            classification = str(previous.get("classification") or "uncertain")
            confidence = float(previous.get("confidence", 0.0) or 0.0)
            required = active_threshold if classification == "active_wheel" else threshold
            if (
                classification in ALERTING
                and confidence >= required
                and not previous.get("notified_at")
            ):
                summary["alerts"].append(_alert_from_record(key, previous))
            continue

        result = ai.ask_json(
            FEATURE_SUSPICIOUS_POST_ANALYSIS,
            system_prompt=(
                "Classify the supplied public post. Return classification, confidence from 0 to 1, and a short reason. "
                "Allowed classifications: active_wheel, possible_wheel_announcement, old_wheel_reference, betboom_promotion, irrelevant, uncertain."
            ),
            user_input=json.dumps(
                {
                    "source": post.source,
                    "published_at": published.isoformat(),
                    "text": post.text[:2400],
                    "direct_event_link_detected": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            fallback_data={
                "classification": "uncertain",
                "confidence": 0.0,
                "reason": "analysis unavailable",
            },
        )
        if not result.ok:
            summary["provider_failures"] += 1
            summary["status"] = result.status
            continue

        classification, confidence, reason = _normalize(result.data)
        was_notified = bool(previous.get("notified_at"))
        required = active_threshold if classification == "active_wheel" else threshold
        should_alert = classification in ALERTING and confidence >= required and not was_notified
        row = {
            "source": post.source,
            "message_id": int(post.message_id),
            "message_url": post.message_url,
            "message_date": published.isoformat(),
            "text_sha256": text_sha,
            "classification": classification,
            "confidence": confidence,
            "reason": reason,
            "decision_id": result.decision_id,
            "analyzed_at": now.isoformat(),
        }
        if previous.get("notified_at"):
            row["notified_at"] = previous["notified_at"]
        records[key] = row
        summary["analyzed"] += 1
        summary["changed"] = True
        if should_alert:
            summary["alerts"].append(_alert_from_record(key, row))
    return summary


def mark_alert_notified(state: dict[str, Any], record_key: str) -> bool:
    records = state.get("posts")
    row = records.get(record_key) if isinstance(records, dict) else None
    if not isinstance(row, dict) or row.get("notified_at"):
        return False
    row["notified_at"] = datetime.now(UTC).isoformat()
    return True


def format_admin_alert(alert: dict[str, Any]) -> str:
    classification = str(alert.get("classification") or "uncertain")
    title = "возможно активное колесо" if classification == "active_wheel" else "возможный анонс колеса"
    confidence = float(alert.get("confidence", 0.0) or 0.0)
    source = str(alert.get("source") or "unknown").lstrip("@")
    reason = str(alert.get("reason") or "нет пояснения")[:500]
    url = str(alert.get("message_url") or "")
    return (
        f"🧠 <b>Служебная AI-диагностика: {title}</b>\n\n"
        f"Источник: <code>@{source}</code>\n"
        f"Уверенность: <b>{confidence:.0%}</b>\n"
        f"Причина: {reason}\n\n"
        "Это только сигнал для просмотра: состояние колёс автоматически не меняется."
        + (f"\nПост: {url}" if url else "")
    )[:4000]


def run_for_messages(monitor_module: Any, messages_by_source: dict[str, list[Any]]) -> dict[str, Any]:
    state = _RUNTIME_STATE
    if not isinstance(state, dict):
        return {
            "status": "state_unavailable",
            "candidates": 0,
            "analyzed": 0,
            "alerts": [],
            "alerts_sent": 0,
            "provider_failures": 0,
            "changed": False,
        }

    posts: list[SuspiciousPost] = []
    for messages in messages_by_source.values():
        for message in messages:
            text = str(getattr(message, "text", "") or "")
            if DIRECT_LINK.search(text):
                continue
            posts.append(
                SuspiciousPost(
                    source=str(getattr(message, "source", "") or "unknown"),
                    message_id=int(getattr(message, "message_id", 0) or 0),
                    date=getattr(message, "date"),
                    text=text,
                    message_url=str(getattr(message, "message_url", "") or ""),
                )
            )

    summary = analyze_posts(posts, state)
    sent = 0
    for alert in list(summary.get("alerts", [])):
        try:
            monitor_module.send_message(format_admin_alert(alert))
        except Exception as exc:
            print(
                "WARNING AI suspicious-post admin alert failed: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            if mark_alert_notified(state, str(alert.get("record_key") or "")):
                summary["changed"] = True
            sent += 1

    compact = {
        "status": str(summary.get("status") or "unknown"),
        "candidates": int(summary.get("candidates", 0) or 0),
        "analyzed": int(summary.get("analyzed", 0) or 0),
        "alerts_sent": sent,
        "provider_failures": int(summary.get("provider_failures", 0) or 0),
    }
    if state.get("last_summary") != compact:
        state["last_summary"] = compact
        summary["changed"] = True
    return {**summary, "alerts_sent": sent}


def install(monitor_module: Any) -> None:
    global _RUNTIME_STATE
    if getattr(monitor_module, "_bbvg_ai_suspicious_post_analysis_installed", False):
        return

    original_load_stats = monitor_module.data_store.load_stats
    original_fetch_all_sources = monitor_module.fetch_all_sources

    def load_stats_with_ai_state():
        global _RUNTIME_STATE
        stats = original_load_stats()
        _RUNTIME_STATE = ensure_state(stats)
        return stats

    def fetch_all_sources_with_ai(sources):
        result = original_fetch_all_sources(sources)
        try:
            run_for_messages(monitor_module, result[0])
        except Exception as exc:
            print(
                "WARNING AI suspicious-post analysis failed: "
                f"{type(exc).__name__}: {exc}"
            )
        return result

    monitor_module.data_store.load_stats = load_stats_with_ai_state
    monitor_module.fetch_all_sources = fetch_all_sources_with_ai
    monitor_module._bbvg_ai_suspicious_post_analysis_installed = True
