from __future__ import annotations

from datetime import timedelta

import monitor
import notification_router

notification_router.install(monitor)

_original_assess_new = monitor.assess_new_wheel
_original_assess_pending = monitor.assess_pending_wheel


def _notification_first(message, result):
    """Do not block the first notification because BetBoom page detection was inconclusive.

    Page/button inspection is used later by the Telegram panel when it builds the
    current active-wheel list. Initial delivery is based on a fresh Telegram post
    containing a valid wheel URL. Existing monitor deduplication still suppresses
    the same wheel when it is reposted by another source during the configured
    deduplication window.
    """
    age = monitor.message_age(message)

    # Keep authoritative positive results unchanged so known deadlines and methods
    # continue to be shown in the notification.
    if result.should_notify:
        return result

    # A newly published Telegram post with a valid freestream URL must be delivered
    # even when the BetBoom page is temporarily incomplete, redirects to a generic
    # page, or its participation button was not parsed successfully.
    if age <= timedelta(minutes=monitor.MAX_NEW_POST_AGE_MINUTES):
        return monitor.WheelAssessment(
            True,
            result.deadline,
            f"новая уникальная публикация; {result.method}",
            "preliminary",
            result.page_excerpt,
        )

    # Old catch-up posts stay filtered. This prevents historical links from being
    # delivered as new notifications after a source is first added.
    return result


def assess_new_notification_first(message, link, state=None):
    return _notification_first(message, _original_assess_new(message, link, state))


def assess_pending_notification_first(message, link, state=None):
    return _notification_first(message, _original_assess_pending(message, link, state))


monitor.assess_new_wheel = assess_new_notification_first
monitor.assess_pending_wheel = assess_pending_notification_first

if __name__ == "__main__":
    raise SystemExit(monitor.main())
