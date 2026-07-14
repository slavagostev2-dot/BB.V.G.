from __future__ import annotations

import bot_notification_state
import monitor
import nightly_discovery
import notification_navigation
import notification_router
import telegram_transport

notification_router.load_config = bot_notification_state.load_config
notification_router.install(monitor)
notification_navigation.install(monitor)
telegram_transport.install(monitor)

_original_fetch_page = nightly_discovery.fetch_public_channel_page


def fetch_page_on_primary_domain(
    username: str,
    before: int | None = None,
    *,
    attempts: int = 2,
    timeout: int | None = None,
):
    messages = _original_fetch_page(
        username, before, attempts=attempts, timeout=timeout
    )
    return [
        monitor.Message(
            source=message.source,
            message_id=message.message_id,
            date=message.date,
            text=telegram_transport.rewrite_telegram_text(message.text),
            message_url=telegram_transport.public_message_url(
                message.source or username, message.message_id
            ),
        )
        for message in messages
    ]


nightly_discovery.fetch_public_channel_page = fetch_page_on_primary_domain

import source_intelligence  # noqa: E402
import source_intelligence_alerts  # noqa: E402

# source_intelligence.main is executed first by the alert wrapper, then new
# wheel-bearing candidates are sent to administrators for a decision.
if __name__ == "__main__":
    raise SystemExit(source_intelligence_alerts.run(source_intelligence))
