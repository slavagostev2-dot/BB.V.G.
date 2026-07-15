from __future__ import annotations

import json
import importlib.util
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import monitor
import notification_integrity_v2
import notification_router


UTC = timezone.utc


def access_config(*, one_user: bool = False) -> dict[str, Any]:
    users: dict[str, dict[str, Any]] = {
        "1": {"chat_id": "101", "notifications_enabled": True},
    }
    if not one_user:
        users.update(
            {
                "2": {"chat_id": "202", "notifications_enabled": True},
                "3": {"chat_id": "303", "notifications_enabled": True},
                "4": {"chat_id": "404", "notifications_enabled": True},
            }
        )
    return {
        "owner_id": "1",
        "admins": [] if one_user else ["2", "4"],
        "blocked_users": [] if one_user else ["4"],
        "notification_recipients": [row["chat_id"] for row in users.values()],
        "settings": {"notifications": True},
        "users": users,
    }


class NotificationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.original_path = notification_integrity_v2.STATE_PATH
        self.original_secret = os.environ.get("BOT_STATE_KEY")
        self.original_load_config = notification_router.load_config
        self.original_functions = {
            name: getattr(notification_router, name)
            for name in (
                "delivery_key",
                "duplicate_delivery",
                "remember_delivery",
                "claim_delivery",
                "release_delivery",
                "complete_delivery",
            )
        }
        notification_integrity_v2.STATE_PATH = (
            Path(self.temporary.name) / "notification_delivery_state.json"
        )
        notification_integrity_v2._volatile_entries.clear()
        notification_integrity_v2._pending_entries.clear()
        notification_router._delivered.clear()
        notification_router._pending_deliveries.clear()
        os.environ["BOT_STATE_KEY"] = "chapter-3-test-key"
        notification_router.delivery_key = notification_integrity_v2.delivery_digest
        notification_router.duplicate_delivery = notification_integrity_v2.duplicate_delivery
        notification_router.remember_delivery = notification_integrity_v2.remember_delivery
        notification_router.claim_delivery = notification_integrity_v2.claim_delivery
        notification_router.release_delivery = notification_integrity_v2.release_delivery
        notification_router.complete_delivery = notification_integrity_v2.complete_delivery

    def tearDown(self) -> None:
        notification_integrity_v2.STATE_PATH = self.original_path
        notification_integrity_v2._volatile_entries.clear()
        notification_integrity_v2._pending_entries.clear()
        notification_router._delivered.clear()
        notification_router._pending_deliveries.clear()
        notification_router.load_config = self.original_load_config
        for name, value in self.original_functions.items():
            setattr(notification_router, name, value)
        if self.original_secret is None:
            os.environ.pop("BOT_STATE_KEY", None)
        else:
            os.environ["BOT_STATE_KEY"] = self.original_secret
        self.temporary.cleanup()

    @staticmethod
    def fake_monitor(*, fail_chats: set[str] | None = None):
        failures = set(fail_chats or set())

        class FakeMonitor:
            sent: list[dict[str, Any]] = []

            @classmethod
            def telegram_api(cls, method: str, payload: dict[str, Any]) -> dict[str, Any]:
                if method != "sendMessage":
                    raise AssertionError(f"Unexpected Telegram method: {method}")
                if str(payload.get("chat_id")) in failures:
                    raise TimeoutError("simulated Telegram timeout")
                cls.sent.append(dict(payload))
                return {"ok": True, "result": {"message_id": len(cls.sent)}}

        notification_router.install(FakeMonitor)
        return FakeMonitor

    def test_full_detection_to_telegram_and_two_source_deduplication(self) -> None:
        config = access_config()
        notification_router.load_config = lambda: (config, True)
        fake = self.fake_monitor()
        original_send = monitor.send_message
        monitor.send_message = fake.send_message
        try:
            first = monitor.Message(
                source="mechanogun",
                message_id=10,
                date=datetime.now(UTC) - timedelta(minutes=1),
                text="Колесо через 2 часа https://betboom.ru/freestream/wheel-a",
                message_url="https://telegram.me/mechanogun/10",
            )
            second = monitor.Message(
                source="collector",
                message_id=20,
                date=datetime.now(UTC),
                text="https://betboom.ru/freestream/wheel-a?from=collector",
                message_url="https://telegram.me/collector/20",
            )
            monitor.notify_new_link(
                first,
                "https://betboom.ru/freestream/wheel-a",
                datetime.now(UTC) + timedelta(hours=2),
                "test",
                [],
            )
            monitor.notify_new_link(
                second,
                "https://betboom.ru/freestream/wheel-a?from=collector",
                None,
                "test",
                [],
            )
        finally:
            monitor.send_message = original_send

        self.assertEqual({row["chat_id"] for row in fake.sent}, {"101", "202", "303"})
        self.assertEqual(len(fake.sent), 3, "one wheel was delivered twice to a recipient")
        self.assertTrue(all("Новое колесо BetBoom" in row["text"] for row in fake.sent))
        self.assertTrue(all("wheel-a" in str(row.get("reply_markup")) for row in fake.sent))

    def test_technical_failure_is_visible_only_to_owner_and_admin(self) -> None:
        config = access_config()
        notification_router.load_config = lambda: (config, True)
        fake = self.fake_monitor()
        result = fake.send_message("⚠️ <b>Сбой проверки Telegram-источников</b>")
        self.assertEqual(result["result"]["sent"], 2)
        self.assertEqual({row["chat_id"] for row in fake.sent}, {"101", "202"})

    def test_failed_send_releases_claim_and_retry_succeeds(self) -> None:
        config = access_config(one_user=True)
        notification_router.load_config = lambda: (config, True)
        attempts = 0

        class FlakyMonitor:
            sent: list[dict[str, Any]] = []

            @classmethod
            def telegram_api(cls, method: str, payload: dict[str, Any]) -> dict[str, Any]:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise TimeoutError("simulated first failure")
                cls.sent.append(dict(payload))
                return {"ok": True, "result": {"message_id": 1}}

        notification_router.install(FlakyMonitor)
        with self.assertRaises(RuntimeError):
            FlakyMonitor.send_message("🎡 Новое колесо BetBoom", url="https://betboom.ru/freestream/retry")
        result = FlakyMonitor.send_message(
            "🎡 Новое колесо BetBoom", url="https://betboom.ru/freestream/retry"
        )
        self.assertEqual(result["result"]["sent"], 1)
        self.assertEqual(len(FlakyMonitor.sent), 1)

    def test_simultaneous_delivery_claim_sends_once(self) -> None:
        config = access_config(one_user=True)
        notification_router.load_config = lambda: (config, True)
        entered = threading.Event()
        release = threading.Event()

        class SlowMonitor:
            sent: list[dict[str, Any]] = []

            @classmethod
            def telegram_api(cls, method: str, payload: dict[str, Any]) -> dict[str, Any]:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test synchronization timed out")
                cls.sent.append(dict(payload))
                return {"ok": True, "result": {"message_id": 1}}

        notification_router.install(SlowMonitor)
        results: list[dict[str, Any]] = []

        def send() -> None:
            results.append(
                SlowMonitor.send_message(
                    "🎡 Новое колесо BetBoom",
                    url="https://betboom.ru/freestream/concurrent",
                )
            )

        first = threading.Thread(target=send)
        second = threading.Thread(target=send)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        second.join(2)
        release.set()
        first.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(SlowMonitor.sent), 1)
        self.assertEqual(sorted(row["result"]["sent"] for row in results), [0, 1])

    def test_corrupted_delivery_ledger_fails_closed(self) -> None:
        notification_integrity_v2.STATE_PATH.write_text("{broken", encoding="utf-8")
        digest = notification_integrity_v2.delivery_digest(
            "101", "wheels", "wheel", None, secret="chapter-3-test-key"
        )
        with self.assertRaises(notification_integrity_v2.NotificationIntegrityError):
            notification_integrity_v2.claim_delivery(digest)

    def test_old_same_identifier_event_can_be_delivered_again(self) -> None:
        digest = notification_integrity_v2.delivery_digest(
            "101",
            "wheels",
            "wheel:wheels:reused-id",
            None,
            secret="chapter-3-test-key",
        )
        old = datetime.now(UTC) - timedelta(
            seconds=notification_integrity_v2.RETENTION_SECONDS + 60
        )
        notification_integrity_v2.STATE_PATH.write_text(
            json.dumps(
                {
                    "format": notification_integrity_v2.FORMAT,
                    "algorithm": "HMAC-SHA256",
                    "retention_seconds": notification_integrity_v2.RETENTION_SECONDS,
                    "entries": {digest: old.isoformat()},
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(notification_integrity_v2.claim_delivery(digest))
        notification_integrity_v2.release_delivery(digest)

    def test_existing_notification_contracts(self) -> None:
        notification_integrity_v2.self_test()

    def test_unpatched_router_contract_and_edge_cases(self) -> None:
        name = "_chapter3_unpatched_notification_router"
        spec = importlib.util.spec_from_file_location(name, notification_router.__file__)
        if spec is None or spec.loader is None:
            self.fail("unable to load an isolated notification router")
        fresh = importlib.util.module_from_spec(spec)
        sys.modules[name] = fresh
        try:
            spec.loader.exec_module(fresh)
            fresh.self_test()
            previous_fallback = os.environ.get("BOT_CHAT_ID")
            os.environ.pop("BOT_CHAT_ID", None)
            self.assertEqual(fresh.recipients({}, False, "wheels"), [])
            os.environ["BOT_CHAT_ID"] = "fallback-chat"
            try:
                self.assertEqual(
                    fresh.recipients({}, False, "admin_system"), ["fallback-chat"]
                )
            finally:
                if previous_fallback is None:
                    os.environ.pop("BOT_CHAT_ID", None)
                else:
                    os.environ["BOT_CHAT_ID"] = previous_fallback

            hidden_config = {
                "users": {
                    "1": {
                        "chat_id": "101",
                        "hidden_wheels": {
                            "future": {
                                "expires_at": (
                                    datetime.now(UTC) + timedelta(hours=1)
                                ).isoformat()
                            },
                            "expired": {
                                "expires_at": (
                                    datetime.now(UTC) - timedelta(hours=1)
                                ).isoformat()
                            },
                            "invalid": {"expires_at": "not-a-date"},
                        },
                    }
                }
            }
            self.assertTrue(fresh.hidden_for_chat(hidden_config, "101", "future"))
            self.assertFalse(fresh.hidden_for_chat(hidden_config, "101", "expired"))
            self.assertTrue(fresh.hidden_for_chat(hidden_config, "101", "invalid"))
            self.assertEqual(
                fresh.wheel_key_from_message(
                    "",
                    None,
                    {
                        "inline_keyboard": [
                            [{"callback_data": "wheel:inactive:from-button"}]
                        ]
                    },
                ),
                "from-button",
            )
            key = fresh.delivery_key("1", "wheels", "one", None)
            self.assertTrue(fresh.claim_delivery(key))
            self.assertFalse(fresh.claim_delivery(key))
            fresh.release_delivery(key)
            self.assertTrue(fresh.claim_delivery(key))
            fresh.complete_delivery(key)
            self.assertTrue(fresh.duplicate_delivery(key))
        finally:
            sys.modules.pop(name, None)

    def test_integrity_merge_and_invalid_rows(self) -> None:
        first = notification_integrity_v2.delivery_digest(
            "1", "wheels", "first", None, secret="chapter-3-test-key"
        )
        second = notification_integrity_v2.delivery_digest(
            "2", "wheels", "second", None, secret="chapter-3-test-key"
        )
        earlier = datetime.now(UTC) - timedelta(minutes=2)
        later = datetime.now(UTC) - timedelta(minutes=1)
        merged = notification_integrity_v2.merge_states(
            {"entries": {first: earlier.isoformat(), "invalid": "bad"}},
            {"entries": {first: later.isoformat(), second: later.isoformat()}},
        )
        self.assertEqual(merged["entries"][first], later.isoformat())
        self.assertIn(second, merged["entries"])
        self.assertNotIn("invalid", merged["entries"])
        self.assertFalse(notification_integrity_v2.claim_delivery("not-a-digest"))


if __name__ == "__main__":
    unittest.main()
