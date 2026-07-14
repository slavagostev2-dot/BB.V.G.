from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup


MINIMUM_FRESH_UNKNOWN_MINUTES = 360
POST_MARKER_RE = re.compile(r'data-post="([^"/]+)/(\d+)"', re.IGNORECASE)


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


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_telegram_button_links_installed", False):
        return

    def fetch_public_channel_with_buttons(username: str):
        response = monitor_module.request_with_retries(
            "GET",
            monitor_module.telegram_transport.public_source_url(username),
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
    install(monitor)
    assert monitor.FRESH_UNKNOWN_POST_MINUTES >= 360
    print("telegram_post_links_v2 segment parser self-test passed")


if __name__ == "__main__":
    self_test()
