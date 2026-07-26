from __future__ import annotations

import fnmatch
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import monitor_data as data_store
import telegram_transport
import wheel_publications_v2
from bbvg import reconciliation
from bbvg.storage import EventStore, event_id_from_entry


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
SOURCES_PATH = ROOT / "public_sources.txt"
IDENTIFIER_SOURCES_PATH = ROOT / "identifier_sources.json"
CATALOG_PATH = ROOT / "source_catalog.txt"

_EVENT_STORE: EventStore | None = None

UTC = timezone.utc
MOSCOW = ZoneInfo("Europe/Moscow")
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Barnaul"))

REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
WHEEL_API_ATTEMPTS = max(1, min(5, int(os.getenv("WHEEL_API_ATTEMPTS", "3"))))
WHEEL_API_FAILURE_ALERT_THRESHOLD = max(
    2, int(os.getenv("WHEEL_API_FAILURE_ALERT_THRESHOLD", "3"))
)
MAX_WORKERS = max(1, min(24, int(os.getenv("MAX_WORKERS", "12"))))
UNKNOWN_DEDUP_HOURS = 2
DEADLINE_GRACE_MINUTES = max(0, int(os.getenv("DEADLINE_GRACE_MINUTES", "30")))
HEARTBEAT_HOURS = max(1, int(os.getenv("HEARTBEAT_HOURS", "6")))
HEALTH_ALERT_COOLDOWN_HOURS = max(
    1, int(os.getenv("HEALTH_ALERT_COOLDOWN_HOURS", "6"))
)
STATUS_REPORT_HOURS = max(1, int(os.getenv("STATUS_REPORT_HOURS", "12")))
BOT_FEEDBACK_ENABLED = os.getenv("BOT_FEEDBACK_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
BUTTON_CONTEXT_DAYS = max(1, int(os.getenv("BUTTON_CONTEXT_DAYS", "7")))
PARTICIPATION_DELAY_MINUTES = max(
    1, int(os.getenv("PARTICIPATION_DELAY_MINUTES", "10"))
)
KNOWN_REMINDER_BEFORE_MINUTES = max(
    1, int(os.getenv("KNOWN_REMINDER_BEFORE_MINUTES", "60"))
)
UNKNOWN_REMINDER_INTERVAL_MINUTES = max(
    5, int(os.getenv("UNKNOWN_REMINDER_INTERVAL_MINUTES", "30"))
)
UNTIMED_WHEEL_TTL_HOURS = max(
    1, int(os.getenv("UNTIMED_WHEEL_TTL_HOURS", "2"))
)
SOURCE_INACTIVITY_DAYS = max(1, int(os.getenv("SOURCE_INACTIVITY_DAYS", "7")))
SOURCE_INACTIVITY_REPORT_DAYS = max(
    1, int(os.getenv("SOURCE_INACTIVITY_REPORT_DAYS", "7"))
)
BOT_COMMANDS_VERSION = 1
AUTO_RUN = os.getenv("AUTO_RUN", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MANUAL_RUN = os.getenv("MANUAL_RUN", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

NOTIFICATION_KEY_VERSION = 8
MAX_NEW_POST_AGE_MINUTES = max(
    5, int(os.getenv("MAX_NEW_POST_AGE_MINUTES", "360"))
)
NEW_SOURCE_CATCHUP_MINUTES = max(
    0, int(os.getenv("NEW_SOURCE_CATCHUP_MINUTES", "1440"))
)
FRESH_UNKNOWN_POST_MINUTES = max(
    0, int(os.getenv("FRESH_UNKNOWN_POST_MINUTES", "20"))
)
PENDING_RECHECK_HOURS = max(1, int(os.getenv("PENDING_RECHECK_HOURS", "24")))
PENDING_RECHECK_MINUTES = max(1, int(os.getenv("PENDING_RECHECK_MINUTES", "4")))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

BETBOOM_WHEEL_INFO_URL = "https://betboom.ru/api/streamer-wheel/action/get-info"
WHEEL_VERIFICATION_CONFIRMED = "confirmed"
WHEEL_VERIFICATION_FAILED = "failed"

# Telegram can display the domain without a protocol and can hide it behind a button.
LINK_RE = re.compile(
    r"(?<![A-Za-z0-9._-])"
    r"(?:https?://)?(?:www\.)?betboom\.ru/freestream/"
    r"[A-Za-z0-9._~-]+",
    re.IGNORECASE,
)

REL_HOUR_MIN_RE = re.compile(
    r"(?:через|остал\w*|ещ[её]|до\s+(?:прокрутки|старта|розыгрыша))"
    r"[^0-9]{0,40}(\d{1,3})\s*(?:час(?:а|ов)?|ч)"
    r"\s*(?:(\d{1,3})\s*(?:мин(?:ут[ыа]?)?|м))?",
    re.IGNORECASE,
)
REL_MIN_RE = re.compile(
    r"(?:через|остал\w*|ещ[её]|до\s+(?:прокрутки|старта|розыгрыша))"
    r"[^0-9]{0,40}(\d{1,4})\s*(?:мин(?:ут[ыа]?)?|м)",
    re.IGNORECASE,
)
DURATION_RE = re.compile(
    r"(?:активн\w*|действ\w*|в\s+течение)\s+"
    r"(\d{1,4})\s*(?:мин(?:ут[ыа]?)?|м)",
    re.IGNORECASE,
)
CLOCK_RE = re.compile(
    r"(?:крутим|прокрут\w*|розыгрыш|старт|колесо)?"
    r"\s*(?:в|—|-)?\s*"
    r"([01]?\d|2[0-3])[:.]([0-5]\d)"
    r"\s*(?:мск|москва|по\s+мск)",
    re.IGNORECASE,
)
CONTEXT_CLOCK_RE = re.compile(
    r"(?:крутим|прокрут\w*|розыгрыш|старт|колесо|финал)"
    r"[^0-9]{0,40}(?:сегодня\s*)?(?:в\s*)?"
    r"([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)",
    re.IGNORECASE,
)
TOMORROW_CLOCK_RE = re.compile(
    r"завтра[^0-9]{0,40}(?:в\s*)?"
    r"([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)",
    re.IGNORECASE,
)
DATE_CLOCK_RE = re.compile(
    r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?"
    r"[^0-9]{0,30}(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)",
    re.IGNORECASE,
)
@dataclass(frozen=True)
class Message:
    source: str
    message_id: int
    date: datetime
    text: str
    message_url: str


@dataclass(frozen=True)
class WheelInspection:
    status: str
    deadline: datetime | None
    method: str
    page_excerpt: str = ""
    action_id: int | None = None
    available_at: datetime | None = None
    verification_status: str = ""
    server_start_at: datetime | None = None


@dataclass(frozen=True)
class WheelAssessment:
    should_notify: bool
    deadline: datetime | None
    method: str
    status: str
    page_excerpt: str = ""
    action_id: int | None = None
    available_at: datetime | None = None
    verification_status: str = ""
    server_start_at: datetime | None = None


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


def read_list(path: Path) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values

    for raw in lines:
        value = raw.split("#", 1)[0].strip().lstrip("@")
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    state.setdefault("version", 6)
    state.setdefault("initialized_sources", [])
    state.setdefault("seen", {})
    state.setdefault("url_alerts", {})
    state.setdefault("activation_alerts", {})
    state.setdefault("pending_posts", {})
    state.setdefault("health", {})
    state.setdefault("button_contexts", {})
    state.setdefault("manual_overrides", {})
    state.setdefault("telegram_update_offset", 0)
    state.setdefault("active_wheels", {})
    state.setdefault("participating_wheels", {})
    state.setdefault("wheel_action_history", {})
    state.setdefault("bot_commands_version", 0)
    state.setdefault("last_source_inactivity_report_at", None)

    # Migrate old link-keyed formats to one global key per wheel identifier.
    migrated_alerts: dict[str, dict] = {}
    for old_key, entry in state.get("url_alerts", {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            key = wheel_key(old_key) if "://" in old_key else old_key.casefold()
        except Exception:
            key = old_key.casefold()
        previous = migrated_alerts.get(key)
        old_until = parse_datetime(entry.get("suppress_until"))
        previous_until = parse_datetime(previous.get("suppress_until")) if previous else None
        if not previous or (old_until and (not previous_until or old_until > previous_until)):
            migrated_alerts[key] = entry

    for link, value in state.get("recent_url_alerts", {}).items():
        alerted_at = parse_datetime(value)
        if not alerted_at:
            continue
        key = wheel_key(link)
        migrated_alerts.setdefault(
            key,
            {
                "identifier": wheel_identifier(link),
                "url": normalize_url(link),
                "alerted_at": alerted_at.isoformat(),
                "suppress_until": (
                    alerted_at + timedelta(hours=UNKNOWN_DEDUP_HOURS)
                ).isoformat(),
            },
        )
    state["url_alerts"] = migrated_alerts

    state.pop("known_status", None)
    state.pop("recent_url_alerts", None)
    state["version"] = 6
    return state


def event_store() -> EventStore:
    global _EVENT_STORE
    if _EVENT_STORE is None:
        _EVENT_STORE = EventStore()
    return _EVENT_STORE


def save_state(state: dict) -> None:
    seen_cutoff = now_utc() - timedelta(days=180)
    alert_cutoff = now_utc() - timedelta(days=180)

    state["seen"] = {
        key: value
        for key, value in state.get("seen", {}).items()
        if (parsed := parse_datetime(value)) is None or parsed >= seen_cutoff
    }
    state["url_alerts"] = {
        link: entry
        for link, entry in state.get("url_alerts", {}).items()
        if isinstance(entry, dict)
        and (
            (parsed := parse_datetime(entry.get("alerted_at"))) is None
            or parsed >= alert_cutoff
        )
    }
    state["activation_alerts"] = {
        link: entry
        for link, entry in state.get("activation_alerts", {}).items()
        if isinstance(entry, dict)
        and (
            (parsed := parse_datetime(entry.get("alerted_at"))) is None
            or parsed >= alert_cutoff
        )
    }
    state["pending_posts"] = {
        key: entry
        for key, entry in state.get("pending_posts", {}).items()
        if isinstance(entry, dict)
        and (
            (expires := parse_datetime(entry.get("expires_at"))) is None
            or expires > now_utc()
        )
    }
    button_cutoff = now_utc() - timedelta(days=BUTTON_CONTEXT_DAYS)
    state["button_contexts"] = {
        key: entry
        for key, entry in state.get("button_contexts", {}).items()
        if isinstance(entry, dict)
        and (
            (created := parse_datetime(entry.get("created_at"))) is None
            or created >= button_cutoff
        )
    }
    state["manual_overrides"] = {
        key: entry
        for key, entry in state.get("manual_overrides", {}).items()
        if isinstance(entry, dict)
        and (
            (expires := parse_datetime(entry.get("expires_at"))) is None
            or expires >= now_utc()
        )
    }
    state["active_wheels"] = {
        key: entry
        for key, entry in state.get("active_wheels", {}).items()
        if isinstance(entry, dict)
        and (
            (expires := parse_datetime(entry.get("expires_at"))) is None
            or expires >= now_utc()
        )
    }
    state["participating_wheels"] = {
        key: entry
        for key, entry in state.get("participating_wheels", {}).items()
        if isinstance(entry, dict)
        and (
            (expires := parse_datetime(entry.get("expires_at"))) is None
            or expires >= now_utc()
        )
    }

    data_store.atomic_write_json(STATE_PATH, state)


def normalize_url(raw_url: str) -> str:
    cleaned = html.unescape(raw_url).strip().rstrip(".,;:!?)]}\"'")
    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = "https://" + cleaned

    parts = urlsplit(cleaned)
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def extract_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in LINK_RE.finditer(text or ""):
        link = normalize_url(match.group(0))
        key = link.casefold()
        if key in seen:
            continue
        seen.add(key)
        links.append(link)
    return links


def wheel_identifier(link: str) -> str:
    path = urlsplit(normalize_url(link)).path.rstrip("/")
    return unquote(path.rsplit("/", 1)[-1]).strip()


def wheel_key(link: str) -> str:
    # One wheel can be reposted by many Telegram channels.  The BetBoom
    # identifier, not the Telegram post, is the global duplicate key.
    return wheel_identifier(link).casefold()


def request_with_retries(
    method: str,
    url: str,
    *,
    attempts: int = 3,
    **kwargs,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504}:
                raise requests.HTTPError(
                    f"Temporary HTTP {response.status_code}", response=response
                )
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    assert last_error is not None
    raise last_error


def fetch_public_channel(username: str) -> list[Message]:
    response = request_with_retries(
        "GET",
        telegram_transport.public_source_url(username),
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        allow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    result: list[Message] = []
    for node in soup.select("div.tgme_widget_message[data-post]"):
        data_post = str(node.get("data-post") or "")
        if "/" not in data_post:
            continue

        source, message_id_text = data_post.rsplit("/", 1)
        try:
            message_id = int(message_id_text)
        except ValueError:
            continue

        parts: list[str] = []
        text_node = node.select_one("div.tgme_widget_message_text")
        if text_node is not None:
            parts.append(text_node.get_text("\n", strip=True))

        # Read button and hidden anchor URLs too. LINK_RE filters unrelated links.
        for anchor in node.select("a[href]"):
            href = html.unescape(str(anchor.get("href") or "")).strip()
            if href:
                parts.append(href)

        time_node = node.select_one("time[datetime]")
        try:
            date = (
                datetime.fromisoformat(str(time_node.get("datetime")))
                if time_node
                else now_utc()
            )
        except ValueError:
            date = now_utc()
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)

        result.append(
            Message(
                source=source or username,
                message_id=message_id,
                date=date,
                text=telegram_transport.rewrite_telegram_text(
                    "\n".join(dict.fromkeys(part for part in parts if part))
                ),
                message_url=telegram_transport.public_message_url(
                    source or username, message_id
                ),
            )
        )
    return sorted(result, key=lambda item: item.message_id)


def infer_deadline(text: str, published_at: datetime) -> tuple[datetime | None, str]:
    match = REL_HOUR_MIN_RE.search(text)
    if match:
        return (
            published_at
            + timedelta(hours=int(match.group(1)), minutes=int(match.group(2) or 0)),
            "текст Telegram: относительное время",
        )

    match = REL_MIN_RE.search(text)
    if match:
        return (
            published_at + timedelta(minutes=int(match.group(1))),
            "текст Telegram: относительные минуты",
        )

    match = DURATION_RE.search(text)
    if match:
        return (
            published_at + timedelta(minutes=int(match.group(1))),
            "текст Telegram: длительность",
        )

    lowered = text.lower()
    phrases = (
        ("через полчаса", timedelta(minutes=30), "текст Telegram: полчаса"),
        ("следующие полчаса", timedelta(minutes=30), "текст Telegram: полчаса"),
        ("через час", timedelta(hours=1), "текст Telegram: один час"),
        ("через полтора часа", timedelta(hours=1, minutes=30), "текст Telegram: полтора часа"),
    )
    for phrase, delta, method in phrases:
        if phrase in lowered:
            return published_at + delta, method

    local_post = published_at.astimezone(MOSCOW)

    match = DATE_CLOCK_RE.search(text)
    if match:
        day, month, year_text, hour, minute = match.groups()
        year = int(year_text) if year_text else local_post.year
        if year < 100:
            year += 2000
        try:
            deadline = local_post.replace(
                year=year, month=int(month), day=int(day),
                hour=int(hour), minute=int(minute), second=0, microsecond=0,
            )
        except ValueError:
            deadline = None
        if deadline and deadline < local_post - timedelta(days=2):
            try:
                deadline = deadline.replace(year=deadline.year + 1)
            except ValueError:
                deadline = None
        if deadline:
            return deadline.astimezone(UTC), "текст Telegram: дата и время МСК"

    match = TOMORROW_CLOCK_RE.search(text)
    if match:
        deadline = (local_post + timedelta(days=1)).replace(
            hour=int(match.group(1)), minute=int(match.group(2)),
            second=0, microsecond=0,
        )
        return deadline.astimezone(UTC), "текст Telegram: завтра, время МСК"

    match = CLOCK_RE.search(text) or CONTEXT_CLOCK_RE.search(text)
    if match:
        deadline = local_post.replace(
            hour=int(match.group(1)), minute=int(match.group(2)),
            second=0, microsecond=0,
        )
        if deadline < local_post - timedelta(minutes=2):
            deadline += timedelta(days=1)
        return deadline.astimezone(UTC), "текст Telegram: время МСК"

    return None, "время в тексте Telegram не найдено"


def _wheel_verification_failed(detail: str) -> WheelInspection:
    print(f"WARNING BetBoom wheel verification failed: {detail}")
    return WheelInspection(
        "verification_failed",
        None,
        "проверка BetBoom временно недоступна",
        verification_status=WHEEL_VERIFICATION_FAILED,
    )


def _api_error_message(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "").strip()
    return str(error or "").strip()


def _api_action_id(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _api_duration_minutes(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def inspect_wheel_page(link: str) -> WheelInspection:
    """Classify a new wheel using BetBoom's action-info response.

    A successful response is authoritative for new discoveries. Transport,
    HTTP and malformed-response failures remain distinguishable from a
    confirmed inactive wheel so the bot can show one cautious notification
    and retry only that unve…16596 tokens truncated….items()):
        if key in seen:
            pending.pop(key, None)
            changed = True
            continue
        if pending_expired(entry):
            seen[key] = now_utc().isoformat()
            pending.pop(key, None)
            pending_expired_count += 1
            source = str(entry.get("source") or "unknown")
            data_store.increment_stat(stats, source, "pending_expired")
            changed = True
            continue
        if not pending_check_due(entry):
            continue

        pair = visible_items.get(key)
        if pair is None:
            message = pending_message(entry)
            link = str(entry.get("url") or "")
            if message is None or not link:
                seen[key] = now_utc().isoformat()
                pending.pop(key, None)
                changed = True
                continue
        else:
            message, link = pair

        assessment = assess_pending_wheel(message, link, state)
        if maybe_record_unknown_sample(
            unknown_samples, stats, message, link, assessment, reason="pending_recheck"
        ):
            unknown_samples_added += 1

        if assessment.status == "active":
            if is_participating(state, link):
                remember_active_wheel(
                    state, message, link, assessment.deadline, "active",
                    assessment.method, assessment.page_excerpt,
                    action_id=assessment.action_id,
                    available_at=assessment.available_at,
                    verification_status=assessment.verification_status,
                    server_start_at=assessment.server_start_at,
                )
                seen[key] = now_utc().isoformat()
                pending.pop(key, None)
                data_store.increment_stat(stats, message.source, "participated_suppressed")
                changed = True
                continue
            if is_activation_suppressed(state, link):
                seen[key] = now_utc().isoformat()
                pending.pop(key, None)
                duplicates += 1
                data_store.increment_stat(stats, message.source, "duplicates_suppressed")
                changed = True
                continue
            try:
                notify_activation(
                    message,
                    link,
                    assessment.deadline,
                    assessment.method,
                    mappings,
                    state,
                    assessment.page_excerpt,
                    action_id=assessment.action_id,
                    available_at=assessment.available_at,
                    verification_status=assessment.verification_status,
                    server_start_at=assessment.server_start_at,
                )
            except Exception as exc:
                send_errors += 1
                errors.append(
                    f"@{message.source} message {message.message_id}: "
                    f"activation notification failed: {type(exc).__name__}: {exc}"
                )
                remember_pending(state, key, message, link, "send_error", str(exc))
                changed = True
                continue
            remember_activation(state, link, assessment.deadline)
            remember_alert(state, link, assessment.deadline)
            seen[key] = now_utc().isoformat()
            pending.pop(key, None)
            activation_sent += 1
            data_store.increment_stat(stats, message.source, "activation_sent")
            data_store.set_stat_timestamp(stats, message.source, "last_activation_at")
            changed = True
            continue

        # An edited post can gain a future deadline before the button appears.
        if assessment.should_notify and not pending_initial_notified(entry):
            if is_participating(state, link):
                remember_active_wheel(
                    state, message, link, assessment.deadline, assessment.status,
                    assessment.method, assessment.page_excerpt,
                    action_id=assessment.action_id,
                    available_at=assessment.available_at,
                    verification_status=assessment.verification_status,
                    server_start_at=assessment.server_start_at,
                )
                state.get("pending_posts", {}).pop(key, None)
                seen[key] = now_utc().isoformat()
                changed = True
                continue
            if not is_suppressed(state, link):
                try:
                    notify_new_link(
                        message,
                        link,
                        assessment.deadline,
                        assessment.method,
                        mappings,
                        state,
                        assessment.page_excerpt,
                        action_id=assessment.action_id,
                        available_at=assessment.available_at,
                        verification_status=assessment.verification_status,
                        server_start_at=assessment.server_start_at,
                    )
                except Exception as exc:
                    send_errors += 1
                    errors.append(
                        f"@{message.source} message {message.message_id}: "
                        f"preliminary notification failed: {type(exc).__name__}: {exc}"
                    )
                else:
                    remember_alert(state, link, assessment.deadline)
                    preliminary_sent += 1
                    data_store.increment_stat(stats, message.source, "preliminary_sent")
                    remember_pending(
                        state,
                        key,
                        message,
                        link,
                        assessment.status,
                        assessment.method,
                        initial_notified=True,
                    )
                    changed = True
                    continue

        remember_pending(
            state,
            key,
            message,
            link,
            assessment.status,
            assessment.method,
        )
        if assessment.status == "inactive":
            inactive_waiting += 1
            data_store.increment_stat(stats, message.source, "inactive_checks")
        else:
            unconfirmed_waiting += 1
            data_store.increment_stat(stats, message.source, "unconfirmed_checks")
        changed = True

    for source in sources:
        messages = messages_by_source.get(source)
        if not messages:
            continue

        items = [
            (notification_key(message, link), message, link)
            for message in messages
            for link in extract_links(message.text)
        ]

        if source not in initialized:
            # Baseline old history silently, but allow a small catch-up window for
            # a newly added source. This prevents a just-reported active wheel from
            # being lost during first initialization of the channel.
            stamp = now_utc().isoformat()
            catchup = timedelta(minutes=NEW_SOURCE_CATCHUP_MINUTES)
            for key, message, _ in items:
                if key in seen or key in pending:
                    continue
                if NEW_SOURCE_CATCHUP_MINUTES == 0 or message_age(message) > catchup:
                    seen[key] = stamp
                    stale_skipped += 1
                    data_store.increment_stat(stats, source, "stale_skipped")
                    changed = True
            initialized.add(source)
            initialized_now += 1
            changed = True
            # Do not continue: recent posts in the catch-up window are processed
            # below and are notified only when the normal activity checks allow it.

        for key, message, link in items:
            if key in seen or key in pending:
                continue

            if message_age(message) > timedelta(minutes=MAX_NEW_POST_AGE_MINUTES):
                seen[key] = now_utc().isoformat()
                stale_skipped += 1
                data_store.increment_stat(stats, source, "stale_skipped")
                changed = True
                continue

            data_store.mark_unique_wheel_post(
                stats, source, key, wheel_key(link)
            )
            assessment = assess_new_wheel(message, link, state)
            if maybe_record_unknown_sample(
                unknown_samples, stats, message, link, assessment, reason="new_post"
            ):
                unknown_samples_added += 1

            if assessment.status == "active":
                if is_participating(state, link):
                    remember_active_wheel(
                        state, message, link, assessment.deadline, "active",
                        assessment.method, assessment.page_excerpt,
                        action_id=assessment.action_id,
                        available_at=assessment.available_at,
                        verification_status=assessment.verification_status,
                        server_start_at=assessment.server_start_at,
                    )
                    seen[key] = now_utc().isoformat()
                    data_store.increment_stat(stats, source, "participated_suppressed")
                    changed = True
                    continue
                if is_activation_suppressed(state, link):
                    seen[key] = now_utc().isoformat()
                    duplicates += 1
                    data_store.increment_stat(stats, source, "duplicates_suppressed")
                    changed = True
                    continue
                try:
                    notify_new_link(
                        message,
                        link,
                        assessment.deadline,
                        assessment.method,
                        mappings,
                        state,
                        assessment.page_excerpt,
                        action_id=assessment.action_id,
                        available_at=assessment.available_at,
                        verification_status=assessment.verification_status,
                        server_start_at=assessment.server_start_at,
                    )
                except Exception as exc:
                    send_errors += 1
                    errors.append(
                        f"@{source} message {message.message_id}: "
                        f"notification failed: {type(exc).__name__}: {exc}"
                    )
                    continue
                remember_activation(state, link, assessment.deadline)
                remember_alert(state, link, assessment.deadline)
                seen[key] = now_utc().isoformat()
                activation_sent += 1
                data_store.increment_stat(stats, source, "activation_sent")
                data_store.set_stat_timestamp(stats, source, "last_activation_at")
                changed = True
                continue

            # A new post may produce one preliminary alert. The same post then
            # remains pending and all repeated checks stay silent until activation.
            initial_notified = False
            if assessment.should_notify and is_participating(state, link):
                remember_active_wheel(
                    state, message, link, assessment.deadline, assessment.status,
                    assessment.method, assessment.page_excerpt,
                    action_id=assessment.action_id,
                    available_at=assessment.available_at,
                    verification_status=assessment.verification_status,
                    server_start_at=assessment.server_start_at,
                )
                seen[key] = now_utc().isoformat()
                changed = True
                continue
            if assessment.should_notify and not is_suppressed(state, link):
                try:
                    notify_new_link(
                        message,
                        link,
                        assessment.deadline,
                        assessment.method,
                        mappings,
                        state,
                        assessment.page_excerpt,
                        action_id=assessment.action_id,
                        available_at=assessment.available_at,
                        verification_status=assessment.verification_status,
                        server_start_at=assessment.server_start_at,
                    )
                except Exception as exc:
                    send_errors += 1
                    errors.append(
                        f"@{source} message {message.message_id}: "
                        f"notification failed: {type(exc).__name__}: {exc}"
                    )
                else:
                    remember_alert(state, link, assessment.deadline)
                    preliminary_sent += 1
                    data_store.increment_stat(stats, source, "preliminary_sent")
                    initial_notified = True

            remember_pending(
                state,
                key,
                message,
                link,
                assessment.status,
                assessment.method,
                initial_notified=initial_notified,
            )
            if assessment.status == "inactive":
                inactive_waiting += 1
                data_store.increment_stat(stats, source, "inactive_checks")
            else:
                unconfirmed_waiting += 1
                data_store.increment_stat(stats, source, "unconfirmed_checks")
            changed = True

    state["initialized_sources"] = sorted(initialized)
    state["notification_key_version"] = NOTIFICATION_KEY_VERSION

    try:
        reconciliation_summary = reconciliation.reconcile_candidates(
            durable_store,
            reconciliation.state_generation_candidates(state),
            recovery_reason="monitor_cycle_reconciliation",
        )
        if reconciliation_summary.get("recovered"):
            changed = True
    except Exception as exc:
        reconciliation_summary = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        errors.append(
            "durable reconciliation failed: "
            f"{type(exc).__name__}: {exc}"
        )

    try:
        if process_auto_participation_dispatch(state):
            changed = True
    except Exception as exc:
        errors.append(
            "auto participation post-scan dispatch failed: "
            f"{type(exc).__name__}: {exc}"
        )

    try:
        inactivity_summary = maybe_send_source_inactivity_report(state, stats, sources)
    except Exception as exc:
        inactivity_summary = {"sent": False, "count": 0, "changed": False}
        errors.append(f"source inactivity report failed: {type(exc).__name__}: {exc}")
    if inactivity_summary.get("changed"):
        changed = True

    summary = {
        "sources": len(sources),
        "checked_sources": len(checked_sources),
        "reachable_sources": len(messages_by_source),
        "quarantined_skipped": len(quarantined_skipped),
        "initialized_now": initialized_now,
        "preliminary_sent": preliminary_sent,
        "activation_sent": activation_sent,
        "pending_total": len(pending),
        "pending_expired": pending_expired_count,
        "duplicates_suppressed": duplicates,
        "stale_skipped": stale_skipped,
        "inactive_waiting": inactive_waiting,
        "unconfirmed_waiting": unconfirmed_waiting,
        "unknown_timer_samples_added": unknown_samples_added,
        "source_errors": len(errors),
        "notification_errors": send_errors,
        "callbacks": callback_summary,
        "reminders": reminder_summary,
        "source_inactivity": inactivity_summary,
        "admin_actions": admin_action_summary,
        "active_wheels": len(state.get("active_wheels", {})),
        "legacy_event_migration": legacy_migration,
        "reconciliation": reconciliation_summary,
    }

    if MANUAL_RUN or heartbeat_due(state):
        state["last_heartbeat_at"] = now_utc().isoformat()
        state["last_run_kind"] = "manual" if MANUAL_RUN else "schedule"
        state["last_run_summary"] = summary
        changed = True

    if checked_sources and not messages_by_source and all_failed_alert_due(state):
        try:
            send_message(
                "⚠️ <b>Монитор не смог проверить ни один Telegram-источник</b>\n\n"
                f"Источников к проверке: {len(checked_sources)}\n"
                f"Ошибок: {len(errors)}\n"
                "Проверь журнал GitHub Actions."
            )
            state["health"]["last_all_failed_alert_at"] = now_utc().isoformat()
            changed = True
        except Exception as exc:
            errors.append(f"health alert failed: {type(exc).__name__}: {exc}")

    if automatic_status_due(state):
        try:
            send_message(
                "🤖 <b>Автоматический монитор работает</b>\n\n"
                f"Telegram-источников: {len(sources)}\n"
                f"Проверено сейчас: {len(checked_sources)}\n"
                f"Доступно сейчас: {len(messages_by_source)}\n"
                f"В карантине: {len(quarantined_skipped)}\n"
                f"Новых постов отправлено: {preliminary_sent}\n"
                f"Колёс активировалось: {activation_sent}\n"
                f"Ожидают активности: {len(pending)}\n"
                f"Повторов подавлено: {duplicates}\n"
                f"Ошибок источников: {len(errors)}\n\n"
                "Повторная проверка одного поста проходит без сообщений."
            )
            state["last_automatic_status_at"] = now_utc().isoformat()
            changed = True
        except Exception as exc:
            errors.append(f"automatic status failed: {type(exc).__name__}: {exc}")

    if changed:
        save_state(state)
    try:
        health["functional"] = event_store().health()
    except Exception as exc:
        health["functional"] = {
            "process_health": "degraded",
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
    data_store.save_health(health)
    data_store.save_stats(stats)
    data_store.save_unknown_samples(unknown_samples)

    print(
        f"Sources: {len(sources)}; checked: {len(checked_sources)}; "
        f"reachable: {len(messages_by_source)}; quarantined: {len(quarantined_skipped)}; "
        f"initialized now: {initialized_now}; preliminary: {preliminary_sent}; "
        f"activated: {activation_sent}; pending: {len(pending)}; "
        f"pending expired: {pending_expired_count}; stale skipped: {stale_skipped}; "
        f"duplicates suppressed: {duplicates}; unknown samples: {unknown_samples_added}; "
        f"errors: {len(errors)}"
    )
    for error in errors[:30]:
        print(f"WARNING {error}")

    if MANUAL_RUN:
        send_message(
            "✅ <b>Ручная проверка завершена</b>\n\n"
            f"Telegram-источников: {len(sources)}\n"
            f"Проверено: {len(checked_sources)}\n"
            f"Доступно: {len(messages_by_source)}\n"
            f"В карантине: {len(quarantined_skipped)}\n"
            f"Новых постов отправлено: {preliminary_sent}\n"
            f"Колёс активировалось: {activation_sent}\n"
            f"Ожидают активности: {len(pending)}\n"
            f"Повторов подавлено: {duplicates}\n"
            f"Ошибок: {len(errors)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

