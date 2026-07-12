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


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
SOURCES_PATH = ROOT / "public_sources.txt"
IDENTIFIER_SOURCES_PATH = ROOT / "identifier_sources.json"

UTC = timezone.utc
MOSCOW = ZoneInfo("Europe/Moscow")
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Barnaul"))

REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
MAX_WORKERS = max(1, min(24, int(os.getenv("MAX_WORKERS", "12"))))
UNKNOWN_DEDUP_HOURS = max(1, int(os.getenv("UNKNOWN_DEDUP_HOURS", "24")))
DEADLINE_GRACE_MINUTES = max(0, int(os.getenv("DEADLINE_GRACE_MINUTES", "30")))
HEARTBEAT_HOURS = max(1, int(os.getenv("HEARTBEAT_HOURS", "6")))
HEALTH_ALERT_COOLDOWN_HOURS = max(
    1, int(os.getenv("HEALTH_ALERT_COOLDOWN_HOURS", "6"))
)
STATUS_REPORT_HOURS = max(1, int(os.getenv("STATUS_REPORT_HOURS", "12")))
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

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

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
COUNTDOWN_TRIGGER_RE = re.compile(
    r"(?:остал\w*|до\s+(?:прокрутки|старта|конца|розыгрыша)|"
    r"таймер|countdown|timer)",
    re.IGNORECASE,
)
COUNTDOWN_COLON_RE = re.compile(
    r"(?:остал\w*|до\s+(?:прокрутки|старта|конца|розыгрыша)|countdown|timer)"
    r"[^0-9]{0,80}(?:(\d{1,3}):)?([0-5]?\d):([0-5]\d)",
    re.IGNORECASE,
)
TIMESTAMP_FIELD_RE = re.compile(
    r"(?:end(?:At|Date|Time)?|end_at|end_time|endsAt|finish(?:At|Date|Time)?|"
    r"finish_at|expires(?:At|Date|Time)?|expires_at|expiration(?:At|Date|Time)?|"
    r"deadline|draw(?:At|Date|Time)?|draw_at|spin(?:At|Date|Time)?|spin_at|"
    r"start(?:At|Date|Time)?|start_at)"
    r"[\\\"'\s]{0,8}[:=][\\\"'\s]{0,8}"
    r"(\d{10,13}|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Message:
    source: str
    message_id: int
    date: datetime
    text: str
    message_url: str


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

    state.setdefault("version", 4)
    state.setdefault("initialized_sources", [])
    state.setdefault("seen", {})
    state.setdefault("url_alerts", {})
    state.setdefault("health", {})

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
    state["version"] = 4
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
        f"https://t.me/s/{username}",
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
                text="\n".join(dict.fromkeys(part for part in parts if part)),
                message_url=f"https://t.me/{source or username}/{message_id}",
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


def countdown_deadline(text: str, reference: datetime) -> datetime | None:
    trigger = COUNTDOWN_TRIGGER_RE.search(text)
    if trigger:
        tail = text[trigger.end() : trigger.end() + 180]
        values = {"days": 0, "hours": 0, "minutes": 0, "seconds": 0}
        patterns = (
            ("days", r"(\d{1,3})\s*(?:дн(?:я|ей)?|д)\b"),
            ("hours", r"(\d{1,3})\s*(?:час(?:а|ов)?|ч)\b"),
            ("minutes", r"(\d{1,3})\s*(?:мин(?:ут[ыа]?)?|м)\b"),
            ("seconds", r"(\d{1,3})\s*(?:сек(?:унд[ыа]?)?|с)\b"),
        )
        for key, pattern in patterns:
            match = re.search(pattern, tail, re.IGNORECASE)
            if match:
                values[key] = int(match.group(1))
        total = timedelta(**values)
        if timedelta(0) < total <= timedelta(days=7):
            return reference + total

    match = COUNTDOWN_COLON_RE.search(text)
    if match:
        hours, minutes, seconds = (int(value or 0) for value in match.groups())
        total = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        if timedelta(0) < total <= timedelta(days=7):
            return reference + total
    return None


def parse_page_timestamp(value: str) -> datetime | None:
    if value.isdigit():
        number = int(value)
        if len(value) == 13:
            number //= 1000
        try:
            parsed = datetime.fromtimestamp(number, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        parsed = parsed.astimezone(UTC)

    now = now_utc()
    return parsed if now - timedelta(hours=2) <= parsed <= now + timedelta(days=7) else None


TIME_KEY_RE = re.compile(
    r"(?:end|finish|expire|expiration|deadline|draw|spin|start).*(?:at|date|time)?$",
    re.IGNORECASE,
)
REMAINING_KEY_RE = re.compile(
    r"(?:remaining|countdown|timeleft|secondsleft|duration)", re.IGNORECASE
)


def deadline_from_json(value: object, reference: datetime, key: str = "") -> datetime | None:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            found = deadline_from_json(child_value, reference, str(child_key))
            if found:
                return found
        return None
    if isinstance(value, list):
        for child in value:
            found = deadline_from_json(child, reference, key)
            if found:
                return found
        return None

    if TIME_KEY_RE.search(key):
        if isinstance(value, (int, float)):
            found = parse_page_timestamp(str(int(value)))
            if found:
                return found
        if isinstance(value, str):
            found = parse_page_timestamp(value.strip())
            if found:
                return found

    if REMAINING_KEY_RE.search(key) and isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 86_400 * 7 and seconds <= 86_400 * 7 * 1000:
            seconds /= 1000
        if 0 < seconds <= 86_400 * 7:
            return reference + timedelta(seconds=seconds)
    return None


def json_deadline_from_soup(soup: BeautifulSoup, reference: datetime) -> datetime | None:
    for script in soup.select('script[type="application/json"], script#__NEXT_DATA__'):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = deadline_from_json(data, reference)
        if found:
            return found
    return None


def page_deadline(link: str) -> tuple[datetime | None, str]:
    """Open only a newly discovered link once; old identifiers are never polled."""
    try:
        response = request_with_retries(
            "GET",
            link,
            attempts=2,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return None, f"страница колеса недоступна: {type(exc).__name__}"

    reference = now_utc()
    raw_html = html.unescape(response.text)
    soup = BeautifulSoup(response.text, "html.parser")
    visible = soup.get_text("\n", strip=True)
    deadline = countdown_deadline(visible, reference) or countdown_deadline(
        raw_html, reference
    )
    if deadline:
        return deadline, "таймер на странице колеса"

    deadline = json_deadline_from_soup(soup, reference)
    if deadline:
        return deadline, "таймер в JSON-данных страницы"

    for node in soup.select("[data-end], [data-deadline], [data-expires], [datetime]"):
        for attr in ("data-end", "data-deadline", "data-expires", "datetime"):
            raw_value = str(node.get(attr) or "").strip()
            if raw_value and (deadline := parse_page_timestamp(raw_value)):
                return deadline, f"атрибут {attr} на странице"

    for match in TIMESTAMP_FIELD_RE.finditer(raw_html):
        deadline = parse_page_timestamp(match.group(1))
        if deadline:
            return deadline, "время окончания в данных страницы"

    return None, "страница открылась, но таймер не найден"


def resolve_deadline(message: Message, link: str) -> tuple[datetime | None, str]:
    post_value, post_method = infer_deadline(message.text, message.date)
    if post_value:
        return post_value, post_method

    # The BetBoom page is opened only once, and only for a newly discovered URL.
    # Historical identifiers such as /freestream/shoke are never polled.
    page_value, page_method = page_deadline(link)
    if page_value:
        return page_value, page_method
    return None, f"{post_method}; {page_method}"


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


def send_message(text: str, url: str | None = None) -> None:
    payload: dict = {
        "chat_id": os.environ["BOT_CHAT_ID"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "Открыть колесо", "url": url}]]
        }
    telegram_api("sendMessage", payload)


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


def notify_new_link(
    message: Message,
    link: str,
    deadline: datetime | None,
    method: str,
    mappings: list[dict],
) -> None:
    identifier_raw = wheel_identifier(link)
    identifier = html.escape(identifier_raw)
    published = message.date.astimezone(DISPLAY_TZ)
    related = related_sources(identifier_raw, mappings)
    related_line = ""
    if related:
        related_line = "Связанные каналы: " + ", ".join(
            f"@{html.escape(source)}" for source in related
        ) + "\n"

    send_message(
        "🎡 <b>Новое колесо BetBoom</b>\n\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\n"
        f"Идентификатор: <code>{identifier}</code>\n"
        f"{related_line}"
        f"Пост: {published:%d.%m.%Y %H:%M}\n"
        f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>\n"
        f"Определение времени: {html.escape(method)}",
        link,
    )


def fetch_all_sources(
    sources: list[str],
) -> tuple[dict[str, list[Message]], list[str]]:
    results: dict[str, list[Message]] = {}
    errors: list[str] = []
    if not sources:
        return results, errors

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(sources))) as pool:
        futures = {pool.submit(fetch_public_channel, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                messages = future.result()
            except Exception as exc:
                errors.append(f"@{source}: {type(exc).__name__}: {exc}")
                continue
            if messages:
                results[source] = messages
            else:
                errors.append(f"@{source}: no public messages found")
    return results, errors


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


def main() -> int:
    try:
        validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    state = load_state()
    sources = read_list(SOURCES_PATH)
    mappings = load_identifier_sources()
    initialized = set(state["initialized_sources"])
    seen: dict[str, str] = state["seen"]

    messages_by_source, errors = fetch_all_sources(sources)
    found = 0
    duplicates = 0
    initialized_now = 0
    send_errors = 0
    changed = False

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
            stamp = now_utc().isoformat()
            for key, _, _ in items:
                if key not in seen:
                    seen[key] = stamp
                    changed = True
            initialized.add(source)
            initialized_now += 1
            changed = True
            continue

        for key, message, link in items:
            if key in seen:
                continue

            if is_suppressed(state, link):
                seen[key] = now_utc().isoformat()
                duplicates += 1
                changed = True
                continue

            deadline, method = resolve_deadline(message, link)
            try:
                notify_new_link(message, link, deadline, method, mappings)
            except Exception as exc:
                send_errors += 1
                errors.append(
                    f"@{source} message {message.message_id}: "
                    f"notification failed: {type(exc).__name__}: {exc}"
                )
                continue

            seen[key] = now_utc().isoformat()
            remember_alert(state, link, deadline)
            found += 1
            changed = True

    state["initialized_sources"] = sorted(initialized)

    summary = {
        "sources": len(sources),
        "reachable_sources": len(messages_by_source),
        "initialized_now": initialized_now,
        "new_links": found,
        "duplicates_suppressed": duplicates,
        "source_errors": len(errors),
        "notification_errors": send_errors,
    }

    if MANUAL_RUN or heartbeat_due(state):
        state["last_heartbeat_at"] = now_utc().isoformat()
        state["last_run_kind"] = "manual" if MANUAL_RUN else "schedule"
        state["last_run_summary"] = summary
        changed = True

    if sources and not messages_by_source and all_failed_alert_due(state):
        try:
            send_message(
                "⚠️ <b>Монитор не смог проверить ни один Telegram-источник</b>\n\n"
                f"Источников: {len(sources)}\n"
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
                f"Доступно сейчас: {len(messages_by_source)}\n"
                f"Новых колёс в этой проверке: {found}\n"
                f"Подавлено повторов: {duplicates}\n"
                f"Ошибок источников: {len(errors)}\n\n"
                "Следующая проверка — примерно через 5 минут."
            )
            state["last_automatic_status_at"] = now_utc().isoformat()
            changed = True
        except Exception as exc:
            errors.append(f"automatic status failed: {type(exc).__name__}: {exc}")

    if changed:
        save_state(state)

    print(
        f"Sources: {len(sources)}; reachable: {len(messages_by_source)}; "
        f"initialized now: {initialized_now}; new links: {found}; "
        f"duplicates suppressed: {duplicates}; errors: {len(errors)}"
    )
    for error in errors[:30]:
        print(f"WARNING {error}")

    if MANUAL_RUN:
        send_message(
            "✅ <b>Ручная проверка завершена</b>\n\n"
            f"Telegram-источников: {len(sources)}\n"
            f"Доступно: {len(messages_by_source)}\n"
            f"Новых ссылок: {found}\n"
            f"Подавлено повторов: {duplicates}\n"
            f"Ошибок: {len(errors)}\n\n"
            "Старые ссылки отдельно не проверяются."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
