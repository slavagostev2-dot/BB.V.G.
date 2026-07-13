from __future__ import annotations

from datetime import timedelta

import monitor
import notification_router

notification_router.install(monitor)

_original_assess_new = monitor.assess_new_wheel
_original_assess_pending = monitor.assess_pending_wheel


def _strict_result(message, result):
    """Do not keep a wheel active indefinitely without a real participation signal."""
    age = monitor.message_age(message)

    # A real active button or a future timer remains authoritative.
    if result.status in {"active", "telegram_deadline"}:
        return result

    # Fresh Telegram posts get a short grace period while BetBoom updates the page.
    if age <= timedelta(minutes=30) and result.status not in {"inactive"}:
        return monitor.WheelAssessment(
            True,
            result.deadline,
            f"страховочное уведомление для свежего поста; {result.method}",
            "fresh_unconfirmed",
            result.page_excerpt,
        )

    # After the grace period, absence of an active button/timer means the wheel is over.
    if result.status not in {"active", "telegram_deadline"}:
        return monitor.WheelAssessment(
            False,
            result.deadline,
            f"кнопка участия и действующий таймер не найдены; {result.method}",
            "inactive",
            result.page_excerpt,
        )
    return result


def assess_new_with_strict_confirmation(message, link, state=None):
    return _strict_result(message, _original_assess_new(message, link, state))


def assess_pending_with_strict_confirmation(message, link, state=None):
    return _strict_result(message, _original_assess_pending(message, link, state))


monitor.assess_new_wheel = assess_new_with_strict_confirmation
monitor.assess_pending_wheel = assess_pending_with_strict_confirmation


if __name__ == "__main__":
    raise SystemExit(monitor.main())
