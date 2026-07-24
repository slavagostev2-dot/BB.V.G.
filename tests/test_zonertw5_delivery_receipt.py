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
MARKUP = {
    "inline_keyboard": [
        [{"text": "✅ Участвую", "callback_data": "bb:p:event-token"}]
    ]
}


def _config(
    *,
    hidden: dict | None = None,
    auto_participation: bool = True,
) -> dict:
    user = {
        "id": "1",
        "chat_id": "1",
        "notifications_enabled": True,
        "notification_preferences": {
            "auto_participation": auto_participation,
        },
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


def test_owner_initial_wheel_alert_waits_for_account_availability() -> None:
    assert guard._owner_deferred_chat(
        _config(), True, "wheels", TEXT, MARKUP
    ) == "1"
    assert guard._owner_deferred_chat(
        _config(auto_participation=False), True, "wheels", TEXT, MARKUP
    ) == ""
    assert guard._owner_deferred_chat(
        _config(), True, "wheels", "⏰ Напоминание о колесе BetBoom", MARKUP
    ) == ""


def test_account_availability_deferral_is_valid_silence(monkeypatch) -> None:
    monkeypatch.setattr(notification_router, "load_config", lambda: (_config(), True))
    guard._validate_wheel_delivery(
        {
            "ok": True,
            "result": {
                "sent": 0,
                "owner_deferred": 1,
                "suppressed": True,
                "reason": guard.OWNER_AUTO_PARTICIPATION_DEFER_REASON,
                "kind": "wheels",
            },
        },
        text=TEXT,
        url=URL,
        reply_markup=MARKUP,
    )


def test_owner_is_removed_only_from_current_delivery(monkeypatch) -> None:
    original = notification_router.recipients
    monkeypatch.setattr(
        notification_router,
        "recipients",
        lambda _config, _exists, _kind: ["1", "2"],
    )
    captured: list[str] = []

    def send(_text, url=None, reply_markup=None):
        captured.extend(notification_router.recipients({}, True, "wheels"))
        return {
            "ok": True,
            "result": {
                "sent": len(captured),
                "category": "user",
                "kind": "wheels",
            },
        }

    response = guard._call_without_owner_recipient(
        send,
        "1",
        TEXT,
        URL,
        MARKUP,
    )
    assert captured == ["2"]
    assert response["result"]["owner_deferred"] == 1
    assert notification_router.recipients({}, True, "wheels") == ["1", "2"]
    monkeypatch.setattr(notification_router, "recipients", original)


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
