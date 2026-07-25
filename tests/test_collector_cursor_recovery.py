from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import monitor
import telegram_post_links_v2
import telegram_transport

UTC = timezone.utc


def _message(source: str, message_id: int, url_source: str) -> monitor.Message:
    return monitor.Message(
        source=source,
        message_id=message_id,
        date=datetime(2026, 7, 25, 13, 48, tzinfo=UTC),
        text=f"https://betboom.ru/freestream/wheel-{message_id}",
        message_url=f"https://t.me/{url_source}/{message_id}",
    )


def test_poisoned_redirect_cursor_recovers_configured_namespace() -> None:
    state = {
        "telegram_collector_cursors": {
            "kolesabb": {
                "last_message_id": 71862,
                "last_page_message_id": 71862,
            }
        },
        "wheel_publications": {
            "ctom19": [
                {
                    "source": "kolesaBB",
                    "message_id": 249,
                    "message_url": "https://t.me/kolesaBB/249",
                }
            ]
        },
    }
    results = {
        "kolesaBB": [_message("kolesaBB", 71862, "amam0610")]
    }
    recovered = {
        250: _message("kolesaBB", 250, "kolesaBB"),
        251: _message("kolesaBB", 251, "kolesaBB"),
    }
    fake_monitor = SimpleNamespace(
        now_utc=lambda: datetime(2026, 7, 25, 15, 8, tzinfo=UTC)
    )

    summary = telegram_post_links_v2.recover_collector_message_gaps(
        fake_monitor,
        state,
        results,
        {},
        [],
        ["kolesaBB"],
        collector_sources={"kolesabb"},
        direct_fetcher=lambda _source, message_id: recovered.get(message_id),
    )

    cursor = state["telegram_collector_cursors"]["kolesabb"]
    assert cursor["namespace_reset_from"] == 71862
    assert cursor["namespace_reset_to"] == 249
    assert cursor["last_message_id"] == 251
    assert summary["namespace_resets"]["kolesaBB"] == {
        "from": 71862,
        "to": 249,
    }
    assert [message.message_id for message in results["kolesaBB"]] == [250, 251]


def test_direct_embed_redirect_is_normalized_to_requested_url() -> None:
    page = """
    <div class="tgme_widget_message" data-post="amam0610/71861">
      <div class="tgme_widget_message_text">
        https://betboom.ru/freestream/pomidor1
      </div>
      <time datetime="2026-07-25T13:39:30+00:00"></time>
    </div>
    """

    class Response:
        text = page

        @staticmethod
        def raise_for_status() -> None:
            return None

    fake_monitor = SimpleNamespace(
        Message=monitor.Message,
        UTC=UTC,
        REQUEST_TIMEOUT=20,
        USER_AGENT="test",
        now_utc=lambda: datetime(2026, 7, 25, 15, 8, tzinfo=UTC),
        request_with_retries=lambda *args, **kwargs: Response(),
        telegram_transport=telegram_transport,
    )

    message = telegram_post_links_v2.fetch_direct_public_post(
        fake_monitor,
        "kolesaBB",
        250,
    )

    assert message is not None
    assert message.source == "kolesaBB"
    assert message.message_id == 250
    assert message.message_url == "https://telegram.me/kolesaBB/250"
