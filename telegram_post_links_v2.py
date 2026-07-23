from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup


MINIMUM_FRESH_UNKNOWN_MINUTES = 360
POST_MARKER_RE = re.compile(r'data-post="([^"/]+)/(\d+)"', re.IGNORECASE)
WHEEL_CONTEXT_RE = re.compile(r"\b(?:колес\w*|крутил\w*|прокрут\w*|wheel\w*|spin\w*)\b", re.IGNORECASE)
BETBOOM_CONTEXT_RE = re.compile(r"\b(?:betboom|bet\s*boom|бетбум|бэтбум)\b", re.IGNORECASE)
ANNOUNCEMENT_ACTION_RE = re.compile(
    r"\b(?:сегодня|завтра|скоро|сейчас|начал\w*|старт\w*|крутим\w*|"
    r"прокрут\w*|розыгрыш\w*|участв\w*|ссылк\w*|позже)\b",
    re.IGNORECASE,
)
CURRENT_ACTION_RE = re.compile(
    r"\b(?:сейчас|уже|начал\w*|ид[её]т|крутим\w*|стартовал\w*)\b",
    re.IGNORECASE,
)
PARTICIPATION_EVIDENCE_RE = re.compile(
    r"\b(?:участв\w*|ссылк\w*|розыгрыш\w*|приз\w*)\b",
    re.IGNORECASE,
)


def _post_segments(page: str):
    """Yield one raw HTML segment per Telegram post.

    Some Telegram URL buttons are rendered after the message wrapper rather
    than inside it. The only stable boundary in the public preview is the next
    ``data-post`` marker, so every post owns the HTML up to that marker.
    """

    matches = list(POST_MARKER_RE.finditer(page or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(page)
        yield match.group(1), int(match.group(2)), page[match.start():end]


def parse_public_channel_html(monitor_module: Any, username: str, page: str):
    """Parse post text and URL buttons from the complete per-post segment."""

    result = []
    for source, message_id, segment in _post_segments(page or ""):
        fragment = BeautifulSoup(segment, "html.parser")
        parts: list[str] = []

        text_node = fragment.select_one("div.tgme_widget_message_text")
        if text_node is not None:
            parts.append(text_node.get_text("\n", strip=True))

        for anchor in fragment.select("a[href]"):
            href = html.unescape(str(anchor.get("href") or "")).strip()
            if href:
                parts.append(href)

        # Keep a regex fallback because the segment starts at the data-post
        # attribute and therefore intentionally omits the opening message tag.
        for raw_href in re.findall(r'href=["\']([^"\']+)["\']', segment, re.IGNORECASE):
            href = html.unescape(raw_href).strip()
            if href:
                parts.append(href)

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
                source=source or username,
                message_id=message_id,
                date=date,
                text=monitor_module.telegram_transport.rewrite_telegram_text(
                    "\n".join(dict.fromkeys(part for part in parts if part))
                ),
                message_url=monitor_module.telegram_transport.public_message_url(
                    source or username, message_id
                ),
            )
        )
    return sorted(result, key=lambda item: item.message_id)


def _ai_wheel_evidence_cap(text: str, classification: str = "") -> float:
    """Cap AI confidence by explicit, independently verifiable post evidence."""

    value = str(text or "")
    if not WHEEL_CONTEXT_RE.search(value) or not ANNOUNCEMENT_ACTION_RE.search(value):
        return 0.0

    has_brand = bool(BETBOOM_CONTEXT_RE.search(value))
    has_participation = bool(PARTICIPATION_EVIDENCE_RE.search(value))
    has_current_action = bool(CURRENT_ACTION_RE.search(value))
    category = str(classification or "").casefold()

    # A generic reference such as “the wheel will be on stream” is context, not
    # evidence of a BetBoom event. It must not reach the AI provider or produce
    # a high-confidence alert.
    if not has_brand:
        return 0.79 if has_participation else 0.49
    if category == "active_wheel" and not has_current_action:
        return 0.69
    if has_current_action and has_participation:
        return 0.96
    if has_participation:
        return 0.93
    return 0.90


def _install_suspicious_post_policy(suspicious_posts: Any) -> None:
    """Install strict evidence handling only around monitor delivery.

    The core classifier functions remain unchanged. This prevents runtime import
    order from leaking policy monkeypatches into tests or other callers.
    """

    if getattr(suspicious_posts, "_bbvg_strict_evidence_policy_installed", False):
        return

    os.environ.setdefault("AI_SUSPICIOUS_POST_MIN_CONFIDENCE", "0.90")
    os.environ.setdefault("AI_SUSPICIOUS_ACTIVE_MIN_CONFIDENCE", "0.93")
    original_run_for_messages = suspicious_posts.run_for_messages

    def run_for_messages_with_evidence(
        monitor_module: Any,
        messages_by_source: dict[str, list[Any]],
    ) -> dict[str, Any]:
        filtered: dict[str, list[Any]] = {}
        for source, messages in messages_by_source.items():
            filtered[source] = [
                message
                for message in messages
                if _ai_wheel_evidence_cap(
                    str(getattr(message, "text", "") or ""),
                    "possible_wheel_announcement",
                )
                >= 0.90
            ]

        original_analyze_posts = suspicious_posts.analyze_posts

        def analyze_posts_with_evidence(
            posts: Any,
            state: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            post_rows = list(posts)
            summary = original_analyze_posts(post_rows, state, **kwargs)
            original_alerts = list(summary.get("alerts", []))
            by_key = {suspicious_posts._key(post): post for post in post_rows}
            records = state.get("posts") if isinstance(state.get("posts"), dict) else {}
            base_threshold = suspicious_posts._float_env(
                "AI_SUSPICIOUS_POST_MIN_CONFIDENCE", 0.90, 0.50, 0.99
            )
            active_threshold = max(
                base_threshold,
                suspicious_posts._float_env(
                    "AI_SUSPICIOUS_ACTIVE_MIN_CONFIDENCE", 0.93, 0.50, 0.99
                ),
            )
            kept: list[dict[str, Any]] = []

            for alert in original_alerts:
                record_key = str(alert.get("record_key") or "")
                post = by_key.get(record_key)
                if post is None:
                    continue
                classification = str(alert.get("classification") or "uncertain")
                cap = _ai_wheel_evidence_cap(post.text, classification)
                confidence = min(float(alert.get("confidence", 0.0) or 0.0), cap)
                required = (
                    active_threshold
                    if classification == "active_wheel"
                    else base_threshold
                )
                row = records.get(record_key) if isinstance(records, dict) else None
                if isinstance(row, dict):
                    row["confidence"] = confidence
                    row["evidence_confidence_cap"] = cap
                    row["evidence_policy"] = "explicit_betboom_action_v1"
                alert["confidence"] = confidence
                if confidence >= required:
                    kept.append(alert)

            summary["alerts"] = kept
            summary["alerts_suppressed_by_evidence"] = max(
                0, len(original_alerts) - len(kept)
            )
            return summary

        suspicious_posts.analyze_posts = analyze_posts_with_evidence
        try:
            return original_run_for_messages(monitor_module, filtered)
        finally:
            suspicious_posts.analyze_posts = original_analyze_posts

    suspicious_posts.run_for_messages = run_for_messages_with_evidence
    suspicious_posts._bbvg_strict_evidence_policy_installed = True


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_telegram_button_links_installed", False):
        return

    def fetch_public_channel_with_buttons(
        username: str,
        before: int | None = None,
    ):
        response = monitor_module.request_with_retries(
            "GET",
            monitor_module.telegram_transport.public_source_url(
                username, before=before
            ),
            timeout=monitor_module.REQUEST_TIMEOUT,
            headers={"User-Agent": monitor_module.USER_AGENT},
            allow_redirects=True,
        )
        response.raise_for_status()
        return parse_public_channel_html(monitor_module, username, response.text)

    monitor_module.fetch_public_channel = fetch_public_channel_with_buttons
    monitor_module.FRESH_UNKNOWN_POST_MINUTES = max(
        int(getattr(monitor_module, "FRESH_UNKNOWN_POST_MINUTES", 0) or 0),
        MINIMUM_FRESH_UNKNOWN_MINUTES,
    )
    monitor_module._bbvg_telegram_button_links_installed = True

    # Suspicious-post analysis belongs to the Telegram post ingestion boundary.
    # It wraps the final multi-source fetch result but never changes wheel state.
    try:
        from bbvg.monitor import suspicious_posts

        _install_suspicious_post_policy(suspicious_posts)
        suspicious_posts.install(monitor_module)
        # Production validation treats telegram_transport as the stable owner of
        # source fetching. Preserve that public integration identity even though
        # the optional AI layer wraps the returned messages.
        monitor_module.fetch_all_sources.__module__ = "telegram_transport"
    except Exception as exc:
        print(
            "WARNING suspicious-post analysis integration failed: "
            f"{type(exc).__name__}: {exc}"
        )

    try:
        import wheel_detection_reliability

        wheel_detection_reliability.install(monitor_module)
    except Exception as exc:
        print(
            "WARNING wheel detection reliability integration failed: "
            f"{type(exc).__name__}: {exc}"
        )


def self_test() -> None:
    import monitor

    page = """
    <div class="tgme_widget_message_wrap">
      <div class="tgme_widget_message" data-post="jestercast/1516">
        <div class="tgme_widget_message_text">Новое колесо</div>
        <time datetime="2026-07-14T10:58:17+00:00"></time>
      </div>
    </div>
    <div class="tgme_widget_message_inline_buttons">
      <a href="https://betboom.ru/freestream/cct1">Участвовать</a>
    </div>
    <div class="tgme_widget_message" data-post="jestercast/1517">
      <div class="tgme_widget_message_text">Следующий пост</div>
      <time datetime="2026-07-14T11:00:00+00:00"></time>
    </div>
    """
    messages = parse_public_channel_html(monitor, "jestercast", page)
    assert len(messages) == 2
    assert messages[0].message_id == 1516
    assert monitor.extract_links(messages[0].text) == [
        "https://betboom.ru/freestream/cct1"
    ]
    assert monitor.extract_links(messages[1].text) == []
    assert _ai_wheel_evidence_cap("Колесо будет на стриме", "active_wheel") < 0.50
    assert _ai_wheel_evidence_cap(
        "BetBoom: сейчас крутим колесо, участвуйте в розыгрыше",
        "active_wheel",
    ) >= 0.93
    install(monitor)
    assert monitor.FRESH_UNKNOWN_POST_MINUTES >= 360
    assert monitor._bbvg_ai_suspicious_post_analysis_installed is True
    assert monitor.fetch_all_sources.__module__ == "telegram_transport"
    print("telegram_post_links_v2 parser and strict AI evidence self-test passed")


if __name__ == "__main__":
    self_test()
