from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup


MINIMUM_FRESH_UNKNOWN_MINUTES = 360


def parse_public_channel_html(monitor_module: Any, username: str, page: str):
    """Parse Telegram posts including buttons stored beside the text block.

    Telegram places some inline URL buttons in ``tgme_widget_message_wrap`` as
    siblings of ``div.tgme_widget_message[data-post]``. Reading anchors only
    from the inner message div misses those wheel links.
    """

    soup = BeautifulSoup(page or "", "html.parser")
    result = []
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

        wrapper = node.find_parent("div", class_="tgme_widget_message_wrap")
        anchor_scope = wrapper if wrapper is not None else node
        for anchor in anchor_scope.select("a[href]"):
            href = html.unescape(str(anchor.get("href") or "")).strip()
            if href:
                parts.append(href)

        time_node = node.select_one("time[datetime]")
        try:
            date = (
                datetime.fromisoformat(str(time_node.get("datetime")))
                if time_node
                else monitor_module.now_utc()
            )
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
      <a class="tgme_widget_message_inline_button" href="https://betboom.ru/freestream/cct1">Участвовать</a>
    </div>
    """
    messages = parse_public_channel_html(monitor, "jestercast", page)
    assert len(messages) == 1
    assert messages[0].message_id == 1516
    assert monitor.extract_links(messages[0].text) == [
        "https://betboom.ru/freestream/cct1"
    ]
    install(monitor)
    assert monitor.FRESH_UNKNOWN_POST_MINUTES >= 360
    print("telegram_post_links_v2 wrapper-button parser self-test passed")


if __name__ == "__main__":
    self_test()
