from __future__ import annotations

import hashlib
import re
from typing import Any

import vk_wheel_notifications


class FakeRouter:
    WHEEL_URL_RE = re.compile(
        r"(?:https?://)?(?:www\.)?betboom\.ru/freestream/([A-Za-z0-9._~-]+)",
        re.IGNORECASE,
    )
    claimed: set[str] = set()
    completed: set[str] = set()

    @staticmethod
    def notification_kind(text: str) -> str:
        lowered = str(text).casefold()
        if "напоминание" in lowered:
            return "wheel_final_reminders"
        if "время прокрутки" in lowered:
            return "wheel_draw_alerts"
        return "wheels"

    @staticmethod
    def notification_event_identity(
        kind: str,
        text: str,
        url: str | None,
        reply_markup: dict | None,
    ) -> str:
        match = FakeRouter.WHEEL_URL_RE.search(str(url or text or ""))
        return f"wheel:{kind}:{match.group(1)}:detected" if match else ""

    @staticmethod
    def delivery_key(chat_id: str, kind: str, text: str, url: str | None) -> str:
        return hashlib.sha256(
            f"{chat_id}|{kind}|{text}|{url or ''}".encode("utf-8")
        ).hexdigest()

    @classmethod
    def claim_delivery(cls, key: str) -> bool:
        if key in cls.claimed or key in cls.completed:
            return False
        cls.claimed.add(key)
        return True

    @classmethod
    def release_delivery(cls, key: str) -> None:
        cls.claimed.discard(key)

    @classmethod
    def complete_delivery(cls, key: str) -> None:
        cls.claimed.discard(key)
        cls.completed.add(key)


def setup_function() -> None:
    FakeRouter.claimed.clear()
    FakeRouter.completed.clear()


def test_unconfigured_transport_is_safe_noop() -> None:
    result = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/no-config",
        token="",
        peer_ids=[],
    )
    assert result == {
        "configured": False,
        "eligible": False,
        "sent": 0,
        "failed": 0,
    }


def test_new_wheel_is_sent_once_per_vk_peer() -> None:
    calls: list[dict[str, str]] = []

    def sender(**kwargs: str) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {"response": 1}

    first = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🎡 <b>Новое колесо BetBoom</b>",
        url="https://betboom.ru/freestream/test-wheel",
        token="test-token",
        peer_ids=["10", "20"],
        sender=sender,
    )
    second = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🎡 <b>Новое колесо BetBoom</b>",
        url="https://betboom.ru/freestream/test-wheel",
        token="test-token",
        peer_ids=["10", "20"],
        sender=sender,
    )

    assert first["sent"] == 2
    assert first["failed"] == 0
    assert second["sent"] == 0
    assert second["duplicates"] == 2
    assert {call["peer_id"] for call in calls} == {"10", "20"}
    assert all("<b>" not in call["message"] for call in calls)
    assert all(
        call["message"].endswith("https://betboom.ru/freestream/test-wheel")
        for call in calls
    )


def test_non_initial_wheel_notifications_are_not_sent_to_vk() -> None:
    calls: list[dict[str, str]] = []

    def sender(**kwargs: str) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {"response": 1}

    reminder = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🚨 Напоминание о колесе BetBoom",
        url="https://betboom.ru/freestream/reminder",
        token="test-token",
        peer_ids=["10"],
        sender=sender,
    )
    draw = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🎯 Время прокрутки колеса наступило",
        url="https://betboom.ru/freestream/draw",
        token="test-token",
        peer_ids=["10"],
        sender=sender,
    )

    assert reminder["eligible"] is False
    assert draw["eligible"] is False
    assert calls == []


def test_failed_vk_send_is_retried_and_dedup_claim_is_released(monkeypatch) -> None:
    monkeypatch.setattr(vk_wheel_notifications, "VK_SEND_ATTEMPTS", 2)
    attempts: list[str] = []

    def failing_sender(**kwargs: str) -> dict[str, Any]:
        attempts.append(kwargs["peer_id"])
        raise RuntimeError("temporary VK failure")

    failed = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/retry-wheel",
        token="test-token",
        peer_ids=["10"],
        sender=failing_sender,
    )
    assert failed["failed"] == 1
    assert attempts == ["10", "10"]

    successful_calls: list[str] = []

    def successful_sender(**kwargs: str) -> dict[str, Any]:
        successful_calls.append(kwargs["peer_id"])
        return {"response": 1}

    retry = vk_wheel_notifications.deliver_vk_wheel_notification(
        FakeRouter,
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/retry-wheel",
        token="test-token",
        peer_ids=["10"],
        sender=successful_sender,
    )
    assert retry["sent"] == 1
    assert successful_calls == ["10"]


def test_vk_failure_cannot_break_successful_telegram_delivery(monkeypatch) -> None:
    class FakeMonitor:
        sent = 0

        @classmethod
        def send_message(
            cls,
            text: str,
            url: str | None = None,
            reply_markup: dict | None = None,
        ) -> dict[str, Any]:
            cls.sent += 1
            return {"ok": True, "result": {"sent": 1}}

    def broken_vk(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated VK transport failure")

    monkeypatch.setattr(
        vk_wheel_notifications,
        "deliver_vk_wheel_notification",
        broken_vk,
    )
    vk_wheel_notifications.install(FakeMonitor, FakeRouter)

    result = FakeMonitor.send_message(
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/telegram-safe",
    )
    assert result["ok"] is True
    assert FakeMonitor.sent == 1


def test_telegram_failure_remains_visible(monkeypatch) -> None:
    vk_calls: list[str] = []

    class FailingMonitor:
        @staticmethod
        def send_message(
            text: str,
            url: str | None = None,
            reply_markup: dict | None = None,
        ) -> dict[str, Any]:
            raise TimeoutError("telegram failed")

    def successful_vk(*args: Any, **kwargs: Any) -> dict[str, Any]:
        vk_calls.append("vk")
        return {"configured": True, "eligible": True, "sent": 1, "failed": 0}

    monkeypatch.setattr(
        vk_wheel_notifications,
        "deliver_vk_wheel_notification",
        successful_vk,
    )
    vk_wheel_notifications.install(FailingMonitor, FakeRouter)

    try:
        FailingMonitor.send_message(
            "🎡 Новое колесо BetBoom",
            url="https://betboom.ru/freestream/telegram-failed",
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("Telegram failure must remain visible to the monitor")

    assert vk_calls == ["vk"]
