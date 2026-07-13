from __future__ import annotations

from datetime import timedelta

import monitor
import notification_router

notification_router.install(monitor)

_original_assess_new = monitor.assess_new_wheel
_original_assess_pending = monitor.assess_pending_wheel


def _strict_result(message, result):
    """Keep a wheel live only with a real participation button or future timer."""
    age = monitor.message_age(message)
    method = str(result.method or "").casefold()
    button_confirmed = "активная кнопка" in method
    future_timer = bool(result.deadline and result.deadline > monitor.now_utc())
    text_only_active = result.status == "active" and not button_confirmed and not future_timer

    if button_confirmed or future_timer or result.status == "telegram_deadline":
        return result

    # Generic page text alone is not enough to keep the wheel active.
    if text_only_active:
        result = monitor.WheelAssessment(
            False,
            result.deadline,
            f"общий текст страницы найден, но кнопки участия нет; {result.method}",
            "unconfirmed",
            result.page_excerpt,
        )

    # Fresh Telegram posts get a short grace period while BetBoom updates the page.
    if age <= timedelta(minutes=30) and result.status != "inactive":
        return monitor.WheelAssessment(
            True,
            result.deadline,
            f"страховочное уведомление для свежего поста; {result.method}",
            "fresh_unconfirmed",
            result.page_excerpt,
        )

    return monitor.WheelAssessment(
        False,
        result.deadline,
        f"кнопка участия и действующий таймер не найдены; {result.method}",
        "inactive",
        result.page_excerpt,
    )


def assess_new_with_strict_confirmation(message, link, state=None):
    return _strict_result(message, _original_assess_new(message, link, state))


def assess_pending_with_strict_confirmation(message, link, state=None):
    return _strict_result(message, _original_assess_pending(message, link, state))


monitor.assess_new_wheel = assess_new_with_strict_confirmation
monitor.assess_pending_wheel = assess_pending_with_strict_confirmation


if __name__ == "__main__":
    raise SystemExit(monitor.main())
