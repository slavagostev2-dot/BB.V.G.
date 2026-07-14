from __future__ import annotations

import bot_notification_state
import monitor
import nightly_discovery
import notification_router
import telegram_transport

notification_router.load_config = bot_notification_state.load_config
notification_router.install(monitor)
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


def main() -> int:
    result = nightly_discovery.main()
    state = nightly_discovery.load_discovery_state()
    state["telegram_domain"] = telegram_transport.PRIMARY_DOMAIN
    nightly_discovery.save_discovery_state(state)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
