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


def unified_known_sources() -> tuple[list[str], set[str]]:
    ordered: list[str] = []
    seen: set[str] = set()
    for source in monitor.read_list(source_intelligence.ACTIVE_PATH):
        clean = str(source or "").strip().lstrip("@")
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            ordered.append(clean)
    return ordered, seen


# source_intelligence.main still performs the background discovery scan, but
# the only approved source inventory is public_sources.txt.
source_intelligence.known_sources = unified_known_sources
source_intelligence_retention.install(
    source_intelligence,
    source_intelligence_alerts,
)

if __name__ == "__main__":
    raise SystemExit(source_intelligence_alerts.run(source_intelligence))
