from __future__ import annotations

from datetime import datetime, timezone

import pytest

import notification_delivery_guard as guard
import notification_router


UTC = timezone.utc
TEXT = (
    "🎡 <b>Новое колесо BetBoom</b>\n\n"
    "Идентификатор: <code>zonertw5</code>\n"
    "Пост: 24.07.2026 19:38"
)
URL = "https://betboom.ru/freestream/zonertw5"


def _config(*, hidden: dict | None = None) -> dict:
    user = {
        "id": "1",
        "chat_id": "1",
        "notifications_enabled": True,
    }
    if hidden is not None:
        user["hidden_wheels"] = hidden
    return {
        "owner_id": "1",
        "admins": [],
        "notification_recipients": ["1"],
        "settings": {"notifications": True, "wheel_notifications": True},
        "users": {"1": user},
    }


def test_zero_recipient_delivery_is_not_recorded_as_success(monkeypatch) -> None:
    monkeypatch.setattr(notification_router, "load_config", lambda: (_config(), True))
    monkeypatch.setattr(
        notification_router,
        "delivery_reservation_status",
        lambda _key: "available",
        raising=False,
    )

    with pytest.raises(guard.WheelNotificationNotDelivered, match="sent=0"):
        guard._validate_wheel_delivery(
            {
                "ok": True,
                "result": {
                    "sent": 0,
                    "hidden_skipped": 0,
                    "category": "user",
                    "kind": "wheels",
                },
            },
            text=TEXT,
            url=URL,
            reply_markup=None,
        )


def test_actual_or_already_completed_delivery_is_accepted(monkeypatch) -> None:
    monkeypatch.setattr(notification_router, "load_config", lambda: (_config(), True))
    guard._validate_wheel_delivery(
        {"ok": True, "result": {"sent": 1, "kind": "wheels"}},
        text=TEXT,
        url=URL,
        reply_markup=None,
    )

    monkeypatch.setattr(
        notification_router,
        "delivery_reservation_status",
        lambda _key: "completed",
        raising=False,
    )
    guard._validate_wheel_delivery(
        {
            "ok": True,
            "result": {
                "sent": 0,
                "hidden_skipped": 0,
                "category": "user",
                "kind": "wheels",
            },
        },
        text=TEXT,
        url=URL,
        reply_markup=None,
    )


def test_referral_silence_remains_intentional(monkeypatch) -> None:
    monkeypatch.setattr(notification_router, "load_config", lambda: (_config(), True))
    guard._validate_wheel_delivery(
        {
            "ok": True,
            "result": {
                "sent": 0,
                "suppressed": True,
                "reason": "referral_wheel_notifications_disabled",
                "kind": "wheels",
            },
        },
        text=TEXT,
        url=URL,
        reply_markup=None,
    )


class _Monitor:
    UTC = UTC

    @staticmethod
    def parse_datetime(value):
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def test_old_personal_hide_does_not_hide_reused_zonertw5_generation() -> None:
    config = _config(
        hidden={
            "zonertw5": {
                "hidden_at": "2026-07-23T19:10:00+00:00",
                "expires_at": "2026-08-22T19:10:00+00:00",
            }
        }
    )
    original_hidden = notification_router.hidden_for_chat
    guard._context.event_anchor = datetime(2026, 7, 24, 12, 38, tzinfo=UTC)
    try:
        assert not guard._generation_aware_hidden(
            _Monitor,
            original_hidden,
            config,
            "1",
            "zonertw5",
        )
        guard._context.event_anchor = datetime(2026, 7, 23, 19, 0, tzinfo=UTC)
        assert guard._generation_aware_hidden(
            _Monitor,
            original_hidden,
            config,
            "1",
            "zonertw5",
        )
    finally:
        if hasattr(guard._context, "event_anchor"):
            delattr(guard._context, "event_anchor")
