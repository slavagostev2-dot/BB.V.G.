from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

from bs4 import BeautifulSoup

import telegram_post_links_v2


RECOVERY_MAX_AGE = timedelta(hours=24)
RECOVERY_RETRY_DELAY = timedelta(minutes=5)
RECOVERY_LIMIT = 4
TEXT_SELECTORS = ".tgme_widget_message_text, .tgme_widget_message_caption"
RESULT_CLOCK_RE = re.compile(
    r"\b(?:итоги|результат\w*|победител\w*)\b[^\n\d]{0,48}"
    r"(?:сегодня\s*)?(?:в\s*)?(\d{1,2})[:.](\d{2})\b",
    re.IGNORECASE,
)


def _append_unique(parts: list[str], value: str) -> None:
    cleaned = str(value or "").strip()
    if cleaned and cleaned not in parts:
        parts.append(cleaned)


def parse_public_channel_html_complete(monitor_module: Any, username: str, page: str):
    """Parse every Telegram text/caption block belonging to one post.

    Telegram can split visible post content between more than one text container.
    The previous parser used select_one(), so a timer/prize block rendered in a
    later container disappeared before wheel metadata extraction.
    """

    result = []
    for source, message_id, segment in telegram_post_links_v2._post_segments(page or ""):
        observed_source = source or username
        canonical_source = str(username or observed_source).strip().lstrip("@")
        fragment = BeautifulSoup(segment, "html.parser")
        parts: list[str] = []

        for text_node in fragment.select(TEXT_SELECTORS):
            _append_unique(parts, text_node.get_text("\n", strip=True))

        for anchor in fragment.select("a[href]"):
            href = html.unescape(str(anchor.get("href") or "")).strip()
            _append_unique(parts, href)

        for raw_href in re.findall(r'href=["\']([^"\']+)["\']', segment, re.IGNORECASE):
            _append_unique(parts, html.unescape(raw_href))

        time_node = fragment.select_one("time[datetime]")
        date_text = str(time_node.get("datetime") or "") if time_node else ""
        if not date_text:
            match = re.search(r'<time[^>]+datetime=["\']([^"\']+)', segment, re.IGNORECASE)
            date_text = match.group(1) if match else ""
        try:
            date = datetime.fromisoformat(date_text) if date_text else monitor_module.now_utc()
        except ValueError:
            date = monitor_module.now_utc()
        if date.tzinfo is None:
            date = date.replace(tzinfo=monitor_module.UTC)

        result.append(
            monitor_module.Message(
                source=canonical_source,
                message_id=message_id,
                date=date,
                text=monitor_module.telegram_transport.rewrite_telegram_text("\n".join(parts)),
                message_url=monitor_module.telegram_transport.public_message_url(
                    observed_source, message_id
                ),
            )
        )
    return sorted(result, key=lambda item: item.message_id)


def _infer_deadline_with_result_clock(
    monitor_module: Any,
    original: Callable[[str, datetime], tuple[datetime | None, str]],
    text: str,
    published_at: datetime,
) -> tuple[datetime | None, str]:
    deadline, method = original(text, published_at)
    if deadline is not None:
        return deadline, method

    match = RESULT_CLOCK_RE.search(str(text or ""))
    if not match:
        return None, method
    local_post = published_at.astimezone(monitor_module.MOSCOW)
    try:
        deadline = local_post.replace(
            hour=int(match.group(1)),
            minute=int(match.group(2)),
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None, method
    if deadline < local_post - timedelta(minutes=2):
        deadline += timedelta(days=1)
    return deadline.astimezone(monitor_module.UTC), "текст Telegram: время итогов МСК"


def _same_wheel(monitor_module: Any, wheel_key: str, message: Any) -> bool:
    try:
        links = monitor_module.extract_links(str(getattr(message, "text", "") or ""))
    except Exception:
        return False
    keys = {monitor_module.wheel_key(link) for link in links}
    return str(wheel_key or "").casefold() in {str(value).casefold() for value in keys}


def recover_recent_untimed_wheels(
    monitor_module: Any,
    *,
    direct_fetcher: Callable[[str, int], Any | None] | None = None,
) -> dict[str, int]:
    """Silently enrich recent active wheels whose first Telegram snapshot was partial."""

    fetch_one = direct_fetcher or (
        lambda source, message_id: telegram_post_links_v2.fetch_direct_public_post(
            monitor_module, source, message_id
        )
    )
    state = monitor_module.load_state()
    active = state.get("active_wheels") if isinstance(state.get("active_wheels"), dict) else {}
    now = monitor_module.now_utc()
    summary = {"checked": 0, "text_refreshed": 0, "deadline_recovered": 0}
    changed = False

    for raw_key, entry in active.items():
        if summary["checked"] >= RECOVERY_LIMIT:
            break
        if not isinstance(entry, dict) or monitor_module.parse_datetime(entry.get("deadline")) is not None:
            continue
        source = str(entry.get("source") or "").strip().lstrip("@")
        try:
            message_id = int(entry.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        published = monitor_module.parse_datetime(entry.get("message_date"))
        if not source or message_id <= 0 or published is None:
            continue
        age = now - published
        if age < timedelta(0) or age > RECOVERY_MAX_AGE:
            continue
        last_check = monitor_module.parse_datetime(entry.get("telegram_metadata_recovery_checked_at"))
        if last_check is not None and now - last_check < RECOVERY_RETRY_DELAY:
            continue

        summary["checked"] += 1
        entry["telegram_metadata_recovery_checked_at"] = now.isoformat()
        changed = True
        try:
            message = fetch_one(source, message_id)
        except Exception as exc:
            entry["telegram_metadata_recovery_error"] = f"{type(exc).__name__}: {exc}"[:240]
            continue
        if message is None:
            entry["telegram_metadata_recovery_error"] = "direct Telegram post was not returned"
            continue
        if not _same_wheel(monitor_module, str(raw_key), message):
            entry["telegram_metadata_recovery_error"] = "direct Telegram post wheel mismatch"
            continue

        full_text = str(getattr(message, "text", "") or "").strip()
        if full_text and full_text != str(entry.get("message_text") or ""):
            entry["message_text"] = full_text[:12000]
            entry["metadata_quality"] = "telegram_direct_post_refreshed"
            summary["text_refreshed"] += 1

        deadline, method = monitor_module.infer_deadline(full_text, message.date)
        if deadline is None or deadline <= now:
            entry["telegram_metadata_recovery_error"] = "direct post still has no future deadline"
            continue

        entry["deadline"] = deadline.isoformat()
        entry["expires_at"] = monitor_module.participation_expiry(deadline, current=now).isoformat()
        entry["deadline_source"] = "telegram_direct_post"
        entry["method"] = str(method or "текст Telegram: восстановлено из прямого поста")[:300]
        entry["needs_manual_time"] = False
        entry["metadata_quality"] = "telegram_direct_post_recovered"
        entry.pop("manual_time_waiting_since", None)
        entry.pop("telegram_metadata_recovery_error", None)
        summary["deadline_recovered"] += 1

        participants = state.get("participating_wheels")
        if isinstance(participants, dict):
            participant = participants.get(str(raw_key).casefold()) or participants.get(raw_key)
            if isinstance(participant, dict):
                participant["deadline"] = deadline.isoformat()
                participant["expires_at"] = entry["expires_at"]
                if full_text:
                    participant["message_text"] = full_text[:12000]

        contexts = state.get("button_contexts")
        if isinstance(contexts, dict):
            for context in contexts.values():
                if not isinstance(context, dict):
                    continue
                context_key = str(context.get("wheel_key") or "").casefold()
                context_url = str(context.get("url") or "")
                if not context_key and context_url:
                    try:
                        context_key = monitor_module.wheel_key(context_url).casefold()
                    except Exception:
                        context_key = ""
                if context_key == str(raw_key).casefold():
                    context["deadline"] = deadline.isoformat()
                    if full_text:
                        context["message_text"] = full_text[:12000]

    if changed:
        monitor_module.save_state(state)
    return summary


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_telegram_metadata_hotfix_installed", False):
        return

    telegram_post_links_v2.parse_public_channel_html = parse_public_channel_html_complete

    original_infer = monitor_module.infer_deadline

    def infer_deadline_complete(text: str, published_at: datetime):
        return _infer_deadline_with_result_clock(
            monitor_module, original_infer, text, published_at
        )

    monitor_module.infer_deadline = infer_deadline_complete

    original_main = monitor_module.main

    def main_with_metadata_recovery(*args: Any, **kwargs: Any):
        try:
            summary = recover_recent_untimed_wheels(monitor_module)
            if summary["text_refreshed"] or summary["deadline_recovered"]:
                print(
                    "Recovered Telegram wheel metadata: "
                    f"text={summary['text_refreshed']} deadline={summary['deadline_recovered']}"
                )
        except Exception as exc:
            print(f"WARNING Telegram metadata recovery: {type(exc).__name__}: {exc}")
        return original_main(*args, **kwargs)

    monitor_module.main = main_with_metadata_recovery
    monitor_module._bbvg_telegram_metadata_hotfix_installed = True


def self_test() -> None:
    import monitor

    page = """
    <div class="tgme_widget_message" data-post="mechanogun/36154">
      <div class="tgme_widget_message_text">ЗАЛЕТАЙ НА КОЛЕСО ФРИБЕТОВ<br>10 ПО 1000 20 ПО 500</div>
      <div class="tgme_widget_message_caption">ИТОГИ ЧЕРЕЗ 10 ЧАСОВ</div>
      <a href="https://betboom.ru/freestream/zonertg13">КЛИКАЙ ДЛЯ УЧАСТИЯ</a>
      <time datetime="2026-08-26T14:42:24+00:00"></time>
    </div>
    """
    parsed = parse_public_channel_html_complete(monitor, "mechanogun", page)
    assert len(parsed) == 1
    message = parsed[0]
    assert "10 ПО 1000 20 ПО 500" in message.text
    assert "ИТОГИ ЧЕРЕЗ 10 ЧАСОВ" in message.text
    assert "zonertg13" in message.text
    deadline, _ = monitor.infer_deadline(message.text, message.date)
    assert deadline == datetime(2026, 8, 27, 0, 42, 24, tzinfo=timezone.utc)

    print("telegram wheel metadata hotfix self-test passed")


if __name__ == "__main__":
    self_test()
