from __future__ import annotations

from datetime import datetime, timedelta, timezone

import betboom_participation_browser as browser
import monitor_entry

UTC = timezone.utc


def test_fresh_inactive_api_result_is_still_reported_as_preliminary() -> None:
    now = datetime.now(UTC)
    message = monitor_entry.monitor.Message(
        source="collector",
        message_id=1,
        date=now - timedelta(minutes=1),
        text="https://betboom.ru/freestream/example",
        message_url="https://telegram.me/collector/1",
    )
    result = monitor_entry.monitor.WheelAssessment(
        False,
        None,
        "BetBoom временно вернул inactive",
        "inactive",
        action_id=None,
        verification_status=monitor_entry.monitor.WHEEL_VERIFICATION_CONFIRMED,
        server_start_at=now,
    )

    guarded = monitor_entry._notification_first(message, result)

    assert guarded.should_notify is True
    assert guarded.status == "preliminary"
    assert "inactive" in guarded.method


def test_post_click_layout_is_not_confirmation_when_authentication_is_visible() -> None:
    assert browser._accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
        authentication_required=False,
    )
    assert not browser._accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
        authentication_required=True,
    )
