from __future__ import annotations

import bot_notification_state
import monitor
import notification_navigation
import notification_router
import telegram_transport

notification_router.load_config = bot_notification_state.load_config
notification_router.install(monitor)
notification_navigation.install(monitor)
telegram_transport.install(monitor)

import source_intelligence  # noqa: E402
import source_intelligence_alerts  # noqa: E402
import source_intelligence_retention  # noqa: E402

# source_intelligence.main performs the background discovery scan against the
# single approved inventory in public_sources.txt.
source_intelligence_retention.install(
    source_intelligence,
    source_intelligence_alerts,
)

if __name__ == "__main__":
    raise SystemExit(source_intelligence_alerts.run(source_intelligence))
