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


def test_new_wheel_dispatches_once_and_strips_telegram_html() -> None:
    calls: list[dict[str, str]] = []

    def dispatcher(**kwargs: str) -> bool:
        calls.append(dict(kwargs))
        return True

    first = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🎡 <b>Новое колесо BetBoom</b>",
        url="https://betboom.ru/freestream/test-wheel",
        dispatcher=dispatcher,
    )
    second = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🎡 <b>Новое колесо BetBoom</b>",
        url="https://betboom.ru/freestream/test-wheel",
        dispatcher=dispatcher,
    )

    assert first["dispatched"] is True
    assert second.get("duplicate") is True
    assert len(calls) == 1
    assert calls[0]["message"] == (
        "🎡 Новое колесо BetBoom\n\nhttps://betboom.ru/freestream/test-wheel"
    )


def test_missing_github_runtime_releases_dedup_for_later_retry() -> None:
    first = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/retry-later",
        dispatcher=lambda **kwargs: False,
    )
    assert first["dispatched"] is False

    calls: list[str] = []

    def dispatcher(**kwargs: str) -> bool:
        calls.append(kwargs["event_identity"])
        return True

    retry = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/retry-later",
        dispatcher=dispatcher,
    )
    assert retry["dispatched"] is True
    assert len(calls) == 1


def test_reminders_draw_alerts_and_active_menu_do_not_dispatch_to_vk() -> None:
    calls: list[str] = []

    def dispatcher(**kwargs: str) -> bool:
        calls.append("dispatch")
        return True

    reminder = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🚨 Напоминание о колесе BetBoom",
        url="https://betboom.ru/freestream/reminder",
        dispatcher=dispatcher,
    )
    draw = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🎯 Время прокрутки колеса наступило",
        url="https://betboom.ru/freestream/draw",
        dispatcher=dispatcher,
    )
    active = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "🔥 Активные колёса",
        url="https://betboom.ru/freestream/menu",
        dispatcher=dispatcher,
    )

    assert reminder["eligible"] is False
    assert draw["eligible"] is False
    assert active["eligible"] is False
    assert calls == []


# Production regression from the missed CTOM05 night-wheel notification.
def test_ctom05_style_initial_notification_dispatches_without_exact_title() -> None:
    class StrictRouter(FakeRouter):
        @staticmethod
        def notification_kind(text: str) -> str:
            return "admin_system"

    calls: list[dict[str, str]] = []
    result = vk_wheel_notifications.dispatch_vk_wheel_notification(
        StrictRouter,
        (
            "Колесо для всех ->\n"
            "https://betboom.ru/freestream/CTOM05\n"
            "BetBoom промокод CTOM до 10000 фрибетов"
        ),
        url="https://betboom.ru/freestream/CTOM05",
        dispatcher=lambda **kwargs: calls.append(dict(kwargs)) or True,
    )

    assert result["eligible"] is True
    assert result["dispatched"] is True
    assert len(calls) == 1
    assert calls[0]["event_identity"] == "wheel:wheels:CTOM05:detected"
    assert "https://betboom.ru/freestream/CTOM05" in calls[0]["message"]


def test_wheel_url_inside_system_error_does_not_dispatch_to_vk() -> None:
    calls: list[str] = []
    result = vk_wheel_notifications.dispatch_vk_wheel_notification(
        FakeRouter,
        "Ошибка проверки колеса https://betboom.ru/freestream/broken",
        dispatcher=lambda **kwargs: calls.append("dispatch") or True,
    )

    assert result["eligible"] is False
    assert calls == []


def test_vk_dispatch_failure_cannot_break_successful_telegram(monkeypatch) -> None:
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

    def broken_dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated VK dispatch failure")

    monkeypatch.setattr(
        vk_wheel_notifications,
        "dispatch_vk_wheel_notification",
        broken_dispatch,
    )
    vk_wheel_notifications.install(FakeMonitor, FakeRouter)

    result = FakeMonitor.send_message(
        "🎡 Новое колесо BetBoom",
        url="https://betboom.ru/freestream/telegram-safe",
    )
    assert result["ok"] is True
    assert FakeMonitor.sent == 1


def test_telegram_failure_remains_visible_even_when_vk_dispatch_runs(monkeypatch) -> None:
    vk_calls: list[str] = []

    class FailingMonitor:
        @staticmethod
        def send_message(
            text: str,
            url: str | None = None,
            reply_markup: dict | None = None,
        ) -> dict[str, Any]:
            raise TimeoutError("telegram failed")

    def successful_dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        vk_calls.append("vk")
        return {"eligible": True, "dispatched": True}

    monkeypatch.setattr(
        vk_wheel_notifications,
        "dispatch_vk_wheel_notification",
        successful_dispatch,
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
        raise AssertionError("Telegram failure must remain visible")

    assert vk_calls == ["vk"]


def test_vk_random_id_is_stable_per_event_and_peer() -> None:
    assert vk_wheel_notifications.vk_random_id("event", "10") == (
        vk_wheel_notifications.vk_random_id("event", "10")
    )
    assert vk_wheel_notifications.vk_random_id("event", "10") != (
        vk_wheel_notifications.vk_random_id("event", "20")
    )
