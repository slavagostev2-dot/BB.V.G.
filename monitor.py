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


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
SOURCES_PATH = ROOT / "public_sources.txt"
IDENTIFIER_SOURCES_PATH = ROOT / "identifier_sources.json"
CATALOG_PATH = ROOT / "source_catalog.txt"

UTC = timezone.utc
MOSCOW = ZoneInfo("Europe/Moscow")
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Barnaul"))

REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
WHEEL_API_ATTEMPTS = max(1, min(5, int(os.getenv("WHEEL_API_ATTEMPTS", "3"))))
WHEEL_API_FAILURE_ALERT_THRESHOLD = max(
    2, int(os.getenv("WHEEL_API_FAILURE_ALERT_THRESHOLD", "3"))
)
MAX_WORKERS = max(1, min(24, int(os.getenv("MAX_WORKERS", "12"))))
UNKNOWN_DEDUP_HOURS = max(1, int(os.getenv("UNKNOWN_DEDUP_HOURS", "24")))
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

    temp = STATE_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(STATE_PATH)


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
    and retry only that unverified entry later.
    """

    normalized = normalize_url(link)
    try:
        response = request_with_retries(
            "POST",
            BETBOOM_WHEEL_INFO_URL,
            attempts=WHEEL_API_ATTEMPTS,
            timeout=REQUEST_TIMEOUT,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "x-platform": "web",
            },
            json={"streamer_link": normalized},
        )
    except requests.RequestException as exc:
        return _wheel_verification_failed(f"{type(exc).__name__}: {exc}")

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        return _wheel_verification_failed(
            f"invalid JSON, HTTP {response.status_code}: {type(exc).__name__}"
        )
    if not isinstance(payload, dict):
        return _wheel_verification_failed(
            f"unexpected JSON type {type(payload).__name__}, HTTP {response.status_code}"
        )

    error_message = _api_error_message(payload)
    info = payload.get("info")
    api_code = payload.get("code")
    if not isinstance(info, dict):
        if "не найд" in error_message.casefold():
            return WheelInspection(
                "inactive",
                None,
                "BetBoom не нашёл действующее колесо по этой ссылке",
                verification_status=WHEEL_VERIFICATION_CONFIRMED,
            )
        return _wheel_verification_failed(
            f"API code={api_code!r}, HTTP {response.status_code}, error={error_message!r}"
        )

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        return _wheel_verification_failed(f"{type(exc).__name__}: {exc}")

    action_id = _api_action_id(info.get("action_id"))
    start = parse_datetime(info.get("start_dttm"))
    duration = _api_duration_minutes(
        info.get("duration_min", info.get("duration_in_minutes"))
    )
    deadline = start + timedelta(minutes=duration) if start and duration else None
    reference = now_utc()
    inactive = bool(info.get("is_ended")) or bool(info.get("is_early"))
    if deadline is not None and deadline <= reference:
        inactive = True

    if inactive:
        return WheelInspection(
            "inactive",
            deadline,
            "BetBoom подтвердил, что время участия истекло",
            action_id=action_id,
            verification_status=WHEEL_VERIFICATION_CONFIRMED,
        )

    available_at = start if start and start > reference else None
    return WheelInspection(
        "active",
        deadline,
        "активность и таймер подтверждены BetBoom"
        if deadline
        else "активность подтверждена BetBoom, таймер не указан",
        action_id=action_id,
        available_at=available_at,
        verification_status=WHEEL_VERIFICATION_CONFIRMED,
    )


def record_wheel_api_verification(
    state: dict | None,
    inspection: WheelInspection,
    *,
    checked_at: datetime | None = None,
) -> bool:
    """Persist consecutive authoritative-check failures for incident routing."""

    if not isinstance(state, dict):
        return False
    current = checked_at or now_utc()
    health = state.setdefault("wheel_api_health", {})
    before = dict(health)
    health["last_checked_at"] = current.isoformat()
    health["alert_threshold"] = WHEEL_API_FAILURE_ALERT_THRESHOLD
    if inspection.status == "verification_failed":
        failures = int(health.get("consecutive_failures", 0) or 0) + 1
        health["consecutive_failures"] = failures
        health.setdefault("failure_started_at", current.isoformat())
        health["last_failure_at"] = current.isoformat()
        health["last_error"] = str(inspection.method or "проверка не ответила")[:300]
        if failures >= WHEEL_API_FAILURE_ALERT_THRESHOLD:
            health["status"] = "degraded"
            health.setdefault("degraded_since", current.isoformat())
        else:
            health["status"] = "retrying"
    else:
        health["status"] = "ok"
        health["consecutive_failures"] = 0
        health["last_success_at"] = current.isoformat()
        health.pop("failure_started_at", None)
        health.pop("degraded_since", None)
        health.pop("last_error", None)
    return before != health


def message_age(message: Message) -> timedelta:
    return max(timedelta(0), now_utc() - message.date.astimezone(UTC))


def manual_override(state: dict | None, link: str) -> str | None:
    if not state:
        return None
    entry = state.get("manual_overrides", {}).get(wheel_key(link))
    if not isinstance(entry, dict):
        return None
    expires = parse_datetime(entry.get("expires_at"))
    if expires and expires < now_utc():
        return None
    value = str(entry.get("status") or "").strip().lower()
    return value if value in {"active", "inactive"} else None


def assess_new_wheel(
    message: Message,
    link: str,
    state: dict | None = None,
) -> WheelAssessment:
    post_deadline, post_method = infer_deadline(message.text, message.date)
    inspection = inspect_wheel_page(link)
    record_wheel_api_verification(state, inspection)

    metadata = {
        "page_excerpt": inspection.page_excerpt,
        "action_id": inspection.action_id,
        "available_at": inspection.available_at,
        "verification_status": inspection.verification_status,
    }

    if inspection.status == "inactive":
        return WheelAssessment(
            False, inspection.deadline, inspection.method, "inactive", **metadata
        )

    override = manual_override(state, link)
    if override == "inactive":
        return WheelAssessment(
            False, inspection.deadline, "отмечено неактивным кнопкой бота", "inactive",
            **metadata,
        )
    if override == "active":
        return WheelAssessment(
            True, inspection.deadline, "подтверждено кнопкой бота", "active",
            **metadata,
        )

    if inspection.status == "active":
        deadline = inspection.deadline
        if deadline is None and post_deadline and post_deadline > now_utc():
            deadline = post_deadline
        return WheelAssessment(
            True, deadline, inspection.method, "active", **metadata
        )

    if inspection.status == "verification_failed":
        deadline = post_deadline if post_deadline and post_deadline > now_utc() else None
        return WheelAssessment(
            True,
            deadline,
            f"{inspection.method}; {post_method}" if deadline else inspection.method,
            "verification_failed",
            **metadata,
        )

    return WheelAssessment(
        False, None, inspection.method, "unconfirmed", **metadata
    )


def human_remaining(deadline: datetime | None) -> str:
    if deadline is None:
        return "не определено"
    seconds = int((deadline - now_utc()).total_seconds())
    if seconds <= 0:
        return "время уже наступило"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours} ч {minutes} мин" if hours else f"{max(1, minutes)} мин"


def telegram_api(method: str, payload: dict) -> dict:
    token = os.environ["BOT_TOKEN"]
    response = request_with_retries(
        "POST",
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram Bot API error: {data}")
    return data


def send_message(
    text: str,
    url: str | None = None,
    reply_markup: dict | None = None,
) -> dict:
    payload: dict = {
        "chat_id": os.environ["BOT_CHAT_ID"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    elif url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "Открыть колесо", "url": url}]]
        }
    return telegram_api("sendMessage", payload)


def button_context_token(message: Message, link: str) -> str:
    raw = f"{message.source.casefold()}:{message.message_id}:{wheel_key(link)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]


def participation_expiry(deadline: datetime | None, *, current: datetime | None = None) -> datetime:
    current = current or now_utc()
    if deadline:
        return max(
            deadline + timedelta(minutes=DEADLINE_GRACE_MINUTES),
            current + timedelta(hours=2),
        )
    return current + timedelta(hours=UNTIMED_WHEEL_TTL_HOURS)


def is_participating(state: dict, link_or_key: str) -> bool:
    key = wheel_key(link_or_key) if "://" in link_or_key else link_or_key.casefold()
    entry = state.get("participating_wheels", {}).get(key)
    if not isinstance(entry, dict):
        return False
    expires = parse_datetime(entry.get("expires_at"))
    if expires and expires < now_utc():
        state.get("participating_wheels", {}).pop(key, None)
        return False
    return True


def mark_participating(state: dict, context: dict) -> None:
    key = str(context.get("wheel_key") or "").casefold()
    url = str(context.get("url") or "")
    if not key and url:
        key = wheel_key(url)
    if not key:
        return
    active_entry = state.get("active_wheels", {}).get(key)
    deadline = parse_datetime(active_entry.get("deadline")) if isinstance(active_entry, dict) else None
    stored_url = normalize_url(url) if url else str(active_entry.get("url") if isinstance(active_entry, dict) else "")
    state.setdefault("participating_wheels", {})[key] = {
        "identifier": str(context.get("identifier") or key),
        "url": stored_url,
        "marked_at": now_utc().isoformat(),
        "expires_at": participation_expiry(deadline).isoformat(),
    }
    if isinstance(active_entry, dict):
        active_entry["participating"] = True
        active_entry["participating_at"] = now_utc().isoformat()


def remember_active_wheel(
    state: dict,
    message: Message,
    link: str,
    deadline: datetime | None,
    status: str,
    method: str,
    page_excerpt: str = "",
    *,
    action_id: int | None = None,
    available_at: datetime | None = None,
    verification_status: str = "",
) -> None:
    current = now_utc()
    key = wheel_key(link)
    previous = state.setdefault("active_wheels", {}).get(key)
    first_notified = (
        parse_datetime(previous.get("first_notified_at"))
        if isinstance(previous, dict)
        else None
    ) or current
    entry = dict(previous) if isinstance(previous, dict) else {}
    entry.update(
        {
            "identifier": wheel_identifier(link),
            "url": normalize_url(link),
            "source": message.source,
            "message_id": message.message_id,
            "message_date": message.date.astimezone(UTC).isoformat(),
            "message_url": message.message_url,
            "message_text": message.text[:4000],
            "status": status,
            "method": method[:300],
            "page_excerpt": page_excerpt[:1200],
            "first_notified_at": first_notified.isoformat(),
            "last_notification_at": current.isoformat(),
            "last_checked_at": current.isoformat(),
            "expires_at": participation_expiry(deadline, current=current).isoformat(),
            "button_token": button_context_token(message, link),
            "participating": is_participating(state, key),
        }
    )
    if deadline:
        entry["deadline"] = deadline.isoformat()
    else:
        entry.pop("deadline", None)
    if action_id is not None:
        entry["action_id"] = action_id
        state.setdefault("wheel_action_history", {})[key] = {
            "action_id": action_id,
            "seen_at": current.isoformat(),
        }
    if available_at is not None:
        entry["available_at"] = available_at.isoformat()
    if verification_status:
        entry["verification_status"] = verification_status
    state["active_wheels"][key] = entry


def active_entry_message(entry: dict) -> Message | None:
    try:
        source = str(entry["source"])
        message_id = int(entry["message_id"])
        date = parse_datetime(entry.get("message_date"))
        message_url = str(entry["message_url"])
        text = str(entry.get("message_text") or entry.get("url") or "")
    except (KeyError, TypeError, ValueError):
        return None
    if date is None:
        return None
    return Message(source, message_id, date, text, message_url)


def known_reminder_due(entry: dict, current: datetime | None = None) -> bool:
    current = current or now_utc()
    if parse_datetime(entry.get("known_reminder_sent_at")):
        return False
    deadline = parse_datetime(entry.get("deadline"))
    first_notified = parse_datetime(entry.get("first_notified_at"))
    if not deadline or not first_notified:
        return False
    due_at = max(
        first_notified + timedelta(minutes=PARTICIPATION_DELAY_MINUTES),
        deadline - timedelta(minutes=KNOWN_REMINDER_BEFORE_MINUTES),
    )
    return due_at <= current <= deadline + timedelta(minutes=DEADLINE_GRACE_MINUTES)


def unknown_reminder_due(entry: dict, current: datetime | None = None) -> bool:
    current = current or now_utc()
    if parse_datetime(entry.get("deadline")):
        return False
    previous = (
        parse_datetime(entry.get("last_unknown_reminder_at"))
        or parse_datetime(entry.get("last_notification_at"))
        or parse_datetime(entry.get("first_notified_at"))
    )
    return bool(previous and current - previous >= timedelta(minutes=UNKNOWN_REMINDER_INTERVAL_MINUTES))


def active_wheels_text(state: dict) -> str:
    entries = [entry for entry in state.get("active_wheels", {}).values() if isinstance(entry, dict)]
    if not entries:
        return "🎡 <b>Активных колёс сейчас нет.</b>"
    entries.sort(
        key=lambda item: (
            parse_datetime(item.get("deadline")) is None,
            parse_datetime(item.get("deadline")) or datetime.max.replace(tzinfo=UTC),
            str(item.get("identifier") or "").casefold(),
        )
    )
    lines = [f"🎡 <b>Активные колёса: {len(entries)}</b>", ""]
    for index, entry in enumerate(entries, 1):
        identifier_raw = str(entry.get("identifier") or "без идентификатора")
        identifier = html.escape(identifier_raw)
        source = html.escape(str(entry.get("source") or "неизвестно"))
        deadline = parse_datetime(entry.get("deadline"))
        available_at = parse_datetime(entry.get("available_at"))
        if available_at and available_at > now_utc():
            timing = f"🟡 Участие откроется через {human_remaining(available_at)}"
        elif available_at and entry.get("availability_status") == "available":
            timing = (
                f"🟢 Доступно сейчас · {human_remaining(deadline)}"
                if deadline
                else "🟢 Доступно сейчас · 🔴 время прокрутки неизвестно"
            )
        else:
            timing = human_remaining(deadline) if deadline else "🔴 Время прокрутки неизвестно"
        if entry.get("verification_status") == WHEEL_VERIFICATION_FAILED:
            timing += " · 🟡 проверка временно недоступна"
        participation = "✅ участвую" if is_participating(state, identifier_raw) else "❌ не отмечено"
        lines.append(
            f"{index}. <code>{identifier}</code> — {html.escape(timing)} — {participation}\n"
            f"   источник: @{source}"
        )
    return "\n".join(lines)[:4000]


def active_wheels_markup(state: dict) -> dict:
    rows: list[list[dict]] = []
    entries = [entry for entry in state.get("active_wheels", {}).values() if isinstance(entry, dict)]
    entries.sort(
        key=lambda item: (
            parse_datetime(item.get("deadline")) is None,
            parse_datetime(item.get("deadline")) or datetime.max.replace(tzinfo=UTC),
        )
    )
    for entry in entries[:25]:
        url = str(entry.get("url") or "")
        token = str(entry.get("button_token") or "")
        identifier_raw = str(entry.get("identifier") or "колесо")
        identifier = identifier_raw[:20]
        row: list[dict] = []
        if url:
            row.append({"text": f"🎡 {identifier}", "url": url})
        if token and not is_participating(state, identifier_raw):
            row.append({"text": "✅ Участвую", "callback_data": f"bb:p:{token}"})
        if row:
            rows.append(row)
    rows.append([{"text": "🔄 Обновить список", "callback_data": "bb:l:active"}])
    return {"inline_keyboard": rows}


def send_active_wheels(state: dict) -> None:
    send_message(active_wheels_text(state), reply_markup=active_wheels_markup(state))


def ensure_bot_commands(state: dict) -> bool:
    if int(state.get("bot_commands_version", 0)) >= BOT_COMMANDS_VERSION:
        return False
    try:
        telegram_api(
            "setMyCommands",
            {
                "commands": [
                    {"command": "active", "description": "Показать активные колёса"},
                    {"command": "wheels", "description": "Показать активные колёса"},
                ]
            },
        )
        telegram_api(
            "setChatMenuButton",
            {
                "chat_id": os.environ["BOT_CHAT_ID"],
                "menu_button": {"type": "commands"},
            },
        )
    except Exception as exc:
        print(f"WARNING bot command setup failed: {type(exc).__name__}: {exc}")
        return False
    state["bot_commands_version"] = BOT_COMMANDS_VERSION
    return True


def process_active_wheels(state: dict, stats: dict) -> dict[str, int | bool]:
    result: dict[str, int | bool] = {
        "tracked": 0,
        "known_reminders": 0,
        "unknown_reminders": 0,
        "removed": 0,
        "changed": False,
    }
    active = state.setdefault("active_wheels", {})
    current = now_utc()
    for key, entry in list(active.items()):
        if not isinstance(entry, dict):
            active.pop(key, None)
            result["removed"] = int(result["removed"]) + 1
            result["changed"] = True
            continue
        result["tracked"] = int(result["tracked"]) + 1
        url = str(entry.get("url") or "")
        deadline = parse_datetime(entry.get("deadline"))
        expires = parse_datetime(entry.get("expires_at"))
        if expires and current >= expires:
            active.pop(key, None)
            result["removed"] = int(result["removed"]) + 1
            result["changed"] = True
            continue

        if url:
            try:
                inspection = inspect_wheel_page(url)
            except Exception as exc:
                entry["last_check_error"] = f"{type(exc).__name__}: {exc}"[:300]
            else:
                entry["last_checked_at"] = current.isoformat()
                entry["status"] = inspection.status
                entry["method"] = inspection.method[:300]
                if inspection.page_excerpt:
                    entry["page_excerpt"] = inspection.page_excerpt[:1200]
                if inspection.deadline:
                    deadline = inspection.deadline
                    entry["deadline"] = deadline.isoformat()
                    entry["expires_at"] = participation_expiry(deadline, current=current).isoformat()
                    participant = state.get("participating_wheels", {}).get(key)
                    if isinstance(participant, dict):
                        participant["expires_at"] = participation_expiry(deadline, current=current).isoformat()
                if inspection.status == "inactive":
                    active.pop(key, None)
                    result["removed"] = int(result["removed"]) + 1
                    result["changed"] = True
                    continue
                result["changed"] = True

        if deadline and current > deadline + timedelta(minutes=DEADLINE_GRACE_MINUTES):
            active.pop(key, None)
            result["removed"] = int(result["removed"]) + 1
            result["changed"] = True
            continue
        if is_participating(state, key):
            entry["participating"] = True
            continue

        message = active_entry_message(entry)
        if message is None or not url:
            continue
        if known_reminder_due(entry, current):
            try:
                send_message(
                    "⏰ <b>Напоминание о колесе BetBoom</b>\n\n"
                    f"Идентификатор: <code>{html.escape(str(entry.get('identifier') or key))}</code>\n"
                    f"Источник: @{html.escape(str(entry.get('source') or 'неизвестно'))}\n"
                    f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>\n\n"
                    "Вы ещё не отметили участие.",
                    reply_markup=wheel_reply_markup(
                        state, message, url, active=True, status="reminder",
                        method=str(entry.get("method") or "reminder"),
                        page_excerpt=str(entry.get("page_excerpt") or ""),
                    ),
                )
            except Exception as exc:
                entry["last_reminder_error"] = f"{type(exc).__name__}: {exc}"[:300]
            else:
                entry["known_reminder_sent_at"] = current.isoformat()
                entry["last_notification_at"] = current.isoformat()
                data_store.increment_stat(stats, str(entry.get("source") or "unknown"), "known_deadline_reminders")
                result["known_reminders"] = int(result["known_reminders"]) + 1
                result["changed"] = True
        elif unknown_reminder_due(entry, current):
            try:
                send_message(
                    "⏰ <b>Напоминание о колесе BetBoom</b>\n\n"
                    f"Идентификатор: <code>{html.escape(str(entry.get('identifier') or key))}</code>\n"
                    f"Источник: @{html.escape(str(entry.get('source') or 'неизвестно'))}\n"
                    "⏳ Время прокрутки пока не найдено.\n\n"
                    "Вы ещё не отметили участие; следующее напоминание будет через 30 минут.",
                    reply_markup=wheel_reply_markup(
                        state, message, url, active=True, status="reminder_unknown",
                        method=str(entry.get("method") or "unknown reminder"),
                        page_excerpt=str(entry.get("page_excerpt") or ""),
                    ),
                )
            except Exception as exc:
                entry["last_reminder_error"] = f"{type(exc).__name__}: {exc}"[:300]
            else:
                entry["last_unknown_reminder_at"] = current.isoformat()
                entry["last_notification_at"] = current.isoformat()
                data_store.increment_stat(stats, str(entry.get("source") or "unknown"), "unknown_deadline_reminders")
                result["unknown_reminders"] = int(result["unknown_reminders"]) + 1
                result["changed"] = True
    return result


def register_button_context(
    state: dict,
    message: Message,
    link: str,
    *,
    status: str,
    method: str,
    page_excerpt: str = "",
) -> str:
    token = button_context_token(message, link)
    state.setdefault("button_contexts", {})[token] = {
        "created_at": now_utc().isoformat(),
        "post_key": notification_key(message, link),
        "wheel_key": wheel_key(link),
        "identifier": wheel_identifier(link),
        "url": normalize_url(link),
        "source": message.source,
        "message_id": message.message_id,
        "message_date": message.date.astimezone(UTC).isoformat(),
        "message_url": message.message_url,
        "message_text": message.text[:4000],
        "status": status,
        "method": method[:300],
        "page_excerpt": page_excerpt[:1200],
    }
    return token


def wheel_reply_markup(
    state: dict,
    message: Message,
    link: str,
    *,
    active: bool,
    status: str,
    method: str,
    page_excerpt: str = "",
) -> dict:
    token = register_button_context(
        state, message, link, status=status, method=method, page_excerpt=page_excerpt
    )
    participating = is_participating(state, link)
    participation_text = "✅ Участие отмечено" if participating else "✅ Участвую"
    participation_action = "n" if participating else "p"
    return {
        "inline_keyboard": [
            [{"text": "🎡 Открыть колесо", "url": normalize_url(link)}],
            [
                {"text": participation_text, "callback_data": f"bb:{participation_action}:{token}"},
                {"text": "📋 Активные колёса", "callback_data": "bb:l:active"},
            ],
            [{"text": "📨 Пост", "url": message.message_url}],
        ]
    }


def _callback_allowed(query: dict) -> bool:
    message = query.get("message") if isinstance(query, dict) else None
    chat = message.get("chat") if isinstance(message, dict) else None
    actual = str(chat.get("id")) if isinstance(chat, dict) and chat.get("id") is not None else ""
    return actual == str(os.environ.get("BOT_CHAT_ID", ""))


def process_bot_feedback(
    state: dict,
    unknown_samples: dict,
    stats: dict,
) -> dict[str, int]:
    result = {
        "callbacks": 0,
        "participating": 0,
        "lists": 0,
    }
    if not BOT_FEEDBACK_ENABLED:
        return result
    try:
        payload = {
            "offset": int(state.get("telegram_update_offset", 0)),
            "timeout": 0,
            "allowed_updates": ["callback_query", "message"],
        }
        response = telegram_api("getUpdates", payload)
    except Exception as exc:
        print(f"WARNING callback polling failed: {type(exc).__name__}: {exc}")
        return result

    for update in response.get("result", []):
        if not isinstance(update, dict):
            continue
        update_id = int(update.get("update_id", 0))
        state["telegram_update_offset"] = max(
            int(state.get("telegram_update_offset", 0)), update_id + 1
        )

        incoming = update.get("message")
        if isinstance(incoming, dict):
            chat = incoming.get("chat")
            actual_chat = str(chat.get("id")) if isinstance(chat, dict) else ""
            text = str(incoming.get("text") or "").strip().casefold()
            command = text.split("@", 1)[0].split(maxsplit=1)[0] if text else ""
            if actual_chat == str(os.environ.get("BOT_CHAT_ID", "")) and command in {"/active", "/wheels"}:
                try:
                    send_active_wheels(state)
                except Exception as exc:
                    print(f"WARNING active list failed: {type(exc).__name__}: {exc}")
                else:
                    result["lists"] += 1
            continue

        query = update.get("callback_query")
        if not isinstance(query, dict):
            continue
        query_id = str(query.get("id") or "")
        data = str(query.get("data") or "")
        if not _callback_allowed(query) or not data.startswith("bb:"):
            if query_id:
                try:
                    telegram_api(
                        "answerCallbackQuery",
                        {"callback_query_id": query_id, "text": "Кнопка недоступна."},
                    )
                except Exception:
                    pass
            continue
        parts = data.split(":", 2)
        if len(parts) != 3:
            continue
        action, token = parts[1], parts[2]

        if action == "l":
            try:
                send_active_wheels(state)
            except Exception as exc:
                answer = f"Не удалось показать список: {type(exc).__name__}"
            else:
                result["callbacks"] += 1
                result["lists"] += 1
                answer = "Список активных колёс отправлен."
        else:
            context = state.get("button_contexts", {}).get(token)
            if not isinstance(context, dict):
                answer = "Контекст устарел."
            else:
                result["callbacks"] += 1
                source = str(context.get("source") or "unknown")
                wheel = str(context.get("wheel_key") or "")
                post_key = str(context.get("post_key") or "")
                pending_entry = state.get("pending_posts", {}).get(post_key)
                if action == "p":
                    already = is_participating(state, wheel)
                    mark_participating(state, context)
                    result["participating"] += 1
                    data_store.increment_stat(stats, source, "participation_marks")
                    answer = "Участие уже было отмечено." if already else "Участие отмечено. Напоминаний по этому колесу больше не будет."
                elif action == "n":
                    answer = "Участие уже отмечено."
                elif action in {"c", "a", "i", "t"}:
                    answer = "Эта кнопка больше не используется."
                else:
                    answer = "Неизвестная команда."
        if query_id:
            try:
                telegram_api(
                    "answerCallbackQuery",
                    {"callback_query_id": query_id, "text": answer[:180]},
                )
            except Exception as exc:
                print(f"WARNING callback answer failed: {type(exc).__name__}: {exc}")
    return result

def notification_key(message: Message, link: str) -> str:
    raw = f"{message.source.casefold()}:{message.message_id}:{wheel_key(link)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_identifier_sources() -> list[dict]:
    try:
        data = json.loads(IDENTIFIER_SOURCES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    mappings = data.get("mappings", []) if isinstance(data, dict) else []
    return [item for item in mappings if isinstance(item, dict)]


def related_sources(identifier: str, mappings: list[dict]) -> list[str]:
    result: list[str] = []
    key = identifier.casefold()
    for item in mappings:
        pattern = str(item.get("pattern", "")).casefold()
        if not pattern or not fnmatch.fnmatchcase(key, pattern):
            continue
        for source in item.get("sources", []):
            value = str(source).strip().lstrip("@")
            if value and value.casefold() not in {item.casefold() for item in result}:
                result.append(value)
    return result


def is_suppressed(state: dict, link: str) -> bool:
    entry = state.get("url_alerts", {}).get(wheel_key(link))
    if not isinstance(entry, dict):
        return False
    until = parse_datetime(entry.get("suppress_until"))
    return bool(until and now_utc() < until)


def remember_alert(state: dict, link: str, deadline: datetime | None) -> None:
    alerted_at = now_utc()
    if deadline:
        suppress_until = max(
            deadline + timedelta(minutes=DEADLINE_GRACE_MINUTES),
            alerted_at + timedelta(hours=1),
        )
    else:
        suppress_until = alerted_at + timedelta(hours=UNKNOWN_DEDUP_HOURS)

    entry = {
        "identifier": wheel_identifier(link),
        "url": normalize_url(link),
        "alerted_at": alerted_at.isoformat(),
        "suppress_until": suppress_until.isoformat(),
    }
    if deadline:
        entry["deadline"] = deadline.isoformat()
    state["url_alerts"][wheel_key(link)] = entry


def is_activation_suppressed(state: dict, link: str) -> bool:
    entry = state.get("activation_alerts", {}).get(wheel_key(link))
    if not isinstance(entry, dict):
        return False
    until = parse_datetime(entry.get("suppress_until"))
    return bool(until and now_utc() < until)


def remember_activation(
    state: dict,
    link: str,
    deadline: datetime | None,
) -> None:
    alerted_at = now_utc()
    if deadline:
        suppress_until = max(
            deadline + timedelta(minutes=DEADLINE_GRACE_MINUTES),
            alerted_at + timedelta(hours=1),
        )
    else:
        suppress_until = alerted_at + timedelta(hours=UNKNOWN_DEDUP_HOURS)
    state["activation_alerts"][wheel_key(link)] = {
        "identifier": wheel_identifier(link),
        "url": normalize_url(link),
        "alerted_at": alerted_at.isoformat(),
        "suppress_until": suppress_until.isoformat(),
    }


def remember_filtered(
    state: dict,
    link: str,
    reason: str,
    *,
    inactive: bool,
) -> None:
    checked_at = now_utc()
    suppress_for = timedelta(days=7) if inactive else timedelta(hours=1)
    state["url_alerts"][wheel_key(link)] = {
        "identifier": wheel_identifier(link),
        "url": normalize_url(link),
        "alerted_at": checked_at.isoformat(),
        "suppress_until": (checked_at + suppress_for).isoformat(),
        "status": "inactive" if inactive else "unconfirmed",
        "reason": reason[:300],
    }


def pending_message(entry: dict) -> Message | None:
    try:
        source = str(entry["source"])
        message_id = int(entry["message_id"])
        date = parse_datetime(entry.get("message_date"))
        message_url = str(entry["message_url"])
        text = str(entry.get("message_text") or entry.get("url") or "")
    except (KeyError, TypeError, ValueError):
        return None
    if date is None:
        return None
    return Message(
        source=source,
        message_id=message_id,
        date=date,
        text=text,
        message_url=message_url,
    )


def remember_pending(
    state: dict,
    key: str,
    message: Message,
    link: str,
    status: str,
    reason: str,
    *,
    initial_notified: bool = False,
) -> None:
    now = now_utc()
    previous = state.get("pending_posts", {}).get(key)
    first_seen = (
        parse_datetime(previous.get("first_seen_at"))
        if isinstance(previous, dict)
        else None
    ) or now
    initial_notified_at = (
        parse_datetime(previous.get("initial_notified_at"))
        if isinstance(previous, dict)
        else None
    )
    if initial_notified and initial_notified_at is None:
        initial_notified_at = now
    entry = {
        "source": message.source,
        "message_id": message.message_id,
        "message_date": message.date.astimezone(UTC).isoformat(),
        "message_url": message.message_url,
        "message_text": message.text[:4000],
        "identifier": wheel_identifier(link),
        "url": normalize_url(link),
        "status": status,
        "reason": reason[:300],
        "first_seen_at": first_seen.isoformat(),
        "last_checked_at": now.isoformat(),
        "expires_at": (first_seen + timedelta(hours=PENDING_RECHECK_HOURS)).isoformat(),
    }
    if initial_notified_at:
        entry["initial_notified_at"] = initial_notified_at.isoformat()
    state["pending_posts"][key] = entry


def pending_initial_notified(entry: dict) -> bool:
    return parse_datetime(entry.get("initial_notified_at")) is not None


def pending_check_due(entry: dict) -> bool:
    checked = parse_datetime(entry.get("last_checked_at"))
    return not checked or now_utc() - checked >= timedelta(
        minutes=PENDING_RECHECK_MINUTES
    )


def pending_expired(entry: dict) -> bool:
    expires = parse_datetime(entry.get("expires_at"))
    return bool(expires and now_utc() >= expires)


def assess_pending_wheel(
    message: Message,
    link: str,
    state: dict | None = None,
) -> WheelAssessment:
    post_deadline, post_method = infer_deadline(message.text, message.date)
    inspection = inspect_wheel_page(link)
    record_wheel_api_verification(state, inspection)
    metadata = {
        "page_excerpt": inspection.page_excerpt,
        "action_id": inspection.action_id,
        "available_at": inspection.available_at,
        "verification_status": inspection.verification_status,
    }
    if inspection.status == "inactive":
        return WheelAssessment(
            False, inspection.deadline, inspection.method, "inactive", **metadata
        )

    override = manual_override(state, link)
    if override == "inactive":
        return WheelAssessment(
            False, inspection.deadline, "отмечено неактивным кнопкой бота", "inactive",
            **metadata,
        )
    if override == "active":
        return WheelAssessment(
            True, inspection.deadline, "подтверждено кнопкой бота", "active",
            **metadata,
        )

    if inspection.status == "active":
        deadline = inspection.deadline
        if deadline is None and post_deadline and post_deadline > now_utc():
            deadline = post_deadline
        return WheelAssessment(
            True, deadline, inspection.method, "active", **metadata
        )
    if inspection.status == "verification_failed":
        deadline = post_deadline if post_deadline and post_deadline > now_utc() else None
        return WheelAssessment(
            True,
            deadline,
            f"{inspection.method}; {post_method}" if deadline else inspection.method,
            "verification_failed",
            **metadata,
        )
    return WheelAssessment(
        False, inspection.deadline, inspection.method, inspection.status, **metadata
    )


def notify_new_link(
    message: Message,
    link: str,
    deadline: datetime | None,
    method: str,
    mappings: list[dict],
    state: dict | None = None,
    page_excerpt: str = "",
    *,
    action_id: int | None = None,
    available_at: datetime | None = None,
    verification_status: str = "",
) -> None:
    identifier_raw = wheel_identifier(link)
    identifier = html.escape(identifier_raw)
    published = message.date.astimezone(DISPLAY_TZ)
    timing = (
        f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>"
        if deadline
        else "🔴 <b>Время прокрутки неизвестно</b>"
    )

    verification = (
        "🟡 <b>Проверка активности временно недоступна</b>\n"
        if verification_status == WHEEL_VERIFICATION_FAILED
        else ""
    )
    send_message(
        "🎡 <b>Новое колесо BetBoom</b>\n\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\n"
        f"Идентификатор: <code>{identifier}</code>\n"
        f"Пост: {published:%d.%m.%Y %H:%M}\n"
        f"{verification}"
        f"{timing}",
        reply_markup=(
            wheel_reply_markup(
                state, message, link, active=False, status="preliminary",
                method=method, page_excerpt=page_excerpt
            ) if state is not None else None
        ),
        url=link if state is None else None,
    )
    if state is not None:
        remember_active_wheel(
            state,
            message,
            link,
            deadline,
            "preliminary",
            method,
            page_excerpt,
            action_id=action_id,
            available_at=available_at,
            verification_status=verification_status,
        )


def notify_activation(
    message: Message,
    link: str,
    deadline: datetime | None,
    method: str,
    mappings: list[dict],
    state: dict | None = None,
    page_excerpt: str = "",
    *,
    action_id: int | None = None,
    available_at: datetime | None = None,
    verification_status: str = "",
) -> None:
    identifier_raw = wheel_identifier(link)
    identifier = html.escape(identifier_raw)
    published = message.date.astimezone(DISPLAY_TZ)
    timing = (
        f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>"
        if deadline
        else "🔴 <b>Время прокрутки неизвестно</b>"
    )
    verification = (
        "🟡 <b>Проверка активности временно недоступна</b>\n"
        if verification_status == WHEEL_VERIFICATION_FAILED
        else ""
    )
    send_message(
        "✅ <b>Колесо BetBoom стало активно</b>\n\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\n"
        f"Идентификатор: <code>{identifier}</code>\n"
        f"Пост: {published:%d.%m.%Y %H:%M}\n"
        f"{verification}"
        f"{timing}",
        reply_markup=(
            wheel_reply_markup(
                state, message, link, active=True, status="active",
                method=method, page_excerpt=page_excerpt
            ) if state is not None else None
        ),
        url=link if state is None else None,
    )
    if state is not None:
        remember_active_wheel(
            state,
            message,
            link,
            deadline,
            "active",
            method,
            page_excerpt,
            action_id=action_id,
            available_at=available_at,
            verification_status=verification_status,
        )


def fetch_all_sources(
    sources: list[str],
) -> tuple[dict[str, list[Message]], dict[str, str], list[str]]:
    results: dict[str, list[Message]] = {}
    errors: dict[str, str] = {}
    empty: list[str] = []
    if not sources:
        return results, errors, empty

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(sources))) as pool:
        futures = {pool.submit(fetch_public_channel, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                messages = future.result()
            except Exception as exc:
                errors[source] = f"{type(exc).__name__}: {exc}"
                continue
            if messages:
                results[source] = messages
            else:
                empty.append(source)
    return results, errors, empty


def maybe_record_unknown_sample(
    unknown_samples: dict,
    stats: dict,
    message: Message,
    link: str,
    assessment: WheelAssessment,
    *,
    reason: str = "parser_unknown",
) -> bool:
    if assessment.deadline is not None:
        return False
    if not assessment.page_excerpt and assessment.status not in {
        "active", "unconfirmed", "unknown", "fresh_unconfirmed"
    }:
        return False
    added = data_store.record_unknown_timer_sample(
        unknown_samples,
        source=message.source,
        message_id=message.message_id,
        message_url=message.message_url,
        wheel_url=normalize_url(link),
        wheel_identifier=wheel_identifier(link),
        status=assessment.status,
        method=assessment.method,
        telegram_text=message.text,
        page_excerpt=assessment.page_excerpt,
        reason=reason,
    )
    if added:
        data_store.increment_stat(stats, message.source, "unknown_timer_samples")
    return added


def validate_environment() -> None:
    missing = [
        name for name in ("BOT_TOKEN", "BOT_CHAT_ID") if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError("Missing GitHub Actions secrets: " + ", ".join(missing))


def heartbeat_due(state: dict) -> bool:
    previous = parse_datetime(state.get("last_heartbeat_at"))
    return not previous or now_utc() - previous >= timedelta(hours=HEARTBEAT_HOURS)


def all_failed_alert_due(state: dict) -> bool:
    previous = parse_datetime(state.get("health", {}).get("last_all_failed_alert_at"))
    return not previous or now_utc() - previous >= timedelta(
        hours=HEALTH_ALERT_COOLDOWN_HOURS
    )


def automatic_status_due(state: dict) -> bool:
    if not AUTO_RUN:
        return False
    previous = parse_datetime(state.get("last_automatic_status_at"))
    return not previous or now_utc() - previous >= timedelta(hours=STATUS_REPORT_HOURS)


def source_inactivity_report_due(
    state: dict, current: datetime | None = None
) -> bool:
    if not AUTO_RUN:
        return False
    current = current or now_utc()
    previous = parse_datetime(state.get("last_source_inactivity_report_at"))
    return not previous or current - previous >= timedelta(
        days=SOURCE_INACTIVITY_REPORT_DAYS
    )


def source_inactivity_report_text(
    rows: list[tuple[str, dict, int]],
) -> str:
    lines = [
        f"📭 <b>За {SOURCE_INACTIVITY_DAYS} дней колёса не обнаружены</b>",
        "",
        "Каналы из быстрой проверки:",
    ]
    for index, (source, entry, days) in enumerate(rows, 1):
        last_wheel = parse_datetime(entry.get("last_wheel_post_at"))
        if last_wheel:
            detail = f"последнее колесо: {last_wheel.astimezone(DISPLAY_TZ):%d.%m.%Y}"
        else:
            detail = "колёс не было с начала наблюдения"
        lines.append(
            f"{index}. <code>@{html.escape(source)}</code> — {days} дн.; {detail}"
        )
    lines.extend(
        [
            "",
            "После полной семидневной проверки эти каналы автоматически перейдут в ночной режим.",
        ]
    )
    return "\n".join(lines)[:4000]


def maybe_send_source_inactivity_report(
    state: dict, stats: dict, sources: list[str]
) -> dict[str, int | bool]:
    result: dict[str, int | bool] = {"sent": False, "count": 0, "changed": False}
    current = now_utc()
    if not source_inactivity_report_due(state, current):
        return result
    rows = data_store.sources_without_recent_wheels(
        stats, sources, minimum_days=SOURCE_INACTIVITY_DAYS, at=current
    )
    result["count"] = len(rows)
    if not rows:
        return result
    send_message(source_inactivity_report_text(rows))
    state["last_source_inactivity_report_at"] = current.isoformat()
    result["sent"] = True
    result["changed"] = True
    return result


def process_admin_actions(
    state: dict, health: dict, stats: dict
) -> dict[str, int | bool | str]:
    """Runtime extension point; the production entrypoint installs the queue."""
    del state, health, stats
    return {"loaded": 0, "pending": 0, "applied": 0, "failed": 0, "changed": False}


def main() -> int:
    try:
        validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    state = load_state()
    health = data_store.load_health()
    stats = data_store.load_stats()
    unknown_samples = data_store.load_unknown_samples()
    admin_action_summary = process_admin_actions(state, health, stats)
    commands_changed = ensure_bot_commands(state)
    callback_summary = process_bot_feedback(state, unknown_samples, stats)
    reminder_summary = process_active_wheels(state, stats)

    sources = data_store.operational_sources(read_list(SOURCES_PATH), "fast")
    checked_sources = [
        source for source in sources if data_store.source_due_for_check(health, source)
    ]
    quarantined_skipped = [source for source in sources if source not in checked_sources]
    for source in quarantined_skipped:
        data_store.record_source_check_stats(stats, source, "quarantined_skip")

    mappings = load_identifier_sources()
    initialized = set(state["initialized_sources"])
    seen: dict[str, str] = state["seen"]
    pending: dict[str, dict] = state["pending_posts"]

    messages_by_source, source_errors, empty_sources = fetch_all_sources(checked_sources)
    errors: list[str] = []
    for source in checked_sources:
        if source in messages_by_source:
            messages = messages_by_source[source]
            data_store.record_source_success(health, source, len(messages))
            data_store.record_source_check_stats(stats, source, "ok", len(messages))
        elif source in empty_sources:
            quarantined = data_store.record_source_problem(
                health, source, "empty", "no public messages found"
            )
            data_store.record_source_check_stats(stats, source, "empty")
            errors.append(
                f"@{source}: no public messages found"
                + ("; quarantined" if quarantined else "")
            )
        else:
            detail = source_errors.get(source, "unknown source error")
            quarantined = data_store.record_source_problem(
                health, source, "error", detail
            )
            data_store.record_source_check_stats(stats, source, "error")
            errors.append(
                f"@{source}: {detail}" + ("; quarantined" if quarantined else "")
            )

    visible_items: dict[str, tuple[Message, str]] = {}
    for messages in messages_by_source.values():
        for message in messages:
            for link in extract_links(message.text):
                visible_items[notification_key(message, link)] = (message, link)

    # On a format upgrade, silently baseline every currently visible post.
    if state.get("notification_key_version") != NOTIFICATION_KEY_VERSION:
        stamp = now_utc().isoformat()
        baseline_items = 0
        pending.clear()
        for source, messages in messages_by_source.items():
            for message in messages:
                for link in extract_links(message.text):
                    key = notification_key(message, link)
                    if key not in seen:
                        seen[key] = stamp
                        baseline_items += 1
            initialized.add(source)
        state["initialized_sources"] = sorted(initialized)
        state["notification_key_version"] = NOTIFICATION_KEY_VERSION
        state["last_run_kind"] = "baseline"
        state["last_run_summary"] = {
            "sources": len(sources),
            "checked_sources": len(checked_sources),
            "reachable_sources": len(messages_by_source),
            "quarantined_skipped": len(quarantined_skipped),
            "baseline_items": baseline_items,
            "source_errors": len(errors),
            "callbacks": callback_summary,
            "admin_actions": admin_action_summary,
        }
        save_state(state)
        data_store.save_health(health)
        data_store.save_stats(stats)
        data_store.save_unknown_samples(unknown_samples)
        print(
            f"Baseline initialized: sources={len(sources)}; "
            f"checked={len(checked_sources)}; reachable={len(messages_by_source)}; "
            f"items={baseline_items}; errors={len(errors)}"
        )
        return 0

    preliminary_sent = 0
    activation_sent = 0
    duplicates = 0
    initialized_now = 0
    send_errors = 0
    stale_skipped = 0
    inactive_waiting = 0
    unconfirmed_waiting = 0
    pending_expired_count = 0
    unknown_samples_added = 0
    changed = (
        bool(callback_summary.get("callbacks"))
        or commands_changed
        or bool(reminder_summary.get("changed"))
        or bool(admin_action_summary.get("changed"))
    )

    # Recheck posts that already produced at most one preliminary alert or were
    # held silently. Repeated checks are silent until the page becomes active.
    for key, entry in list(pending.items()):
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
