from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import monitor_data
import notification_integrity_v2
import notification_preferences_v2
import notification_router
import rating_policy
import wheel_publications_v2


def _rating_acceptance() -> None:
    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}

    def decide(wheel: str, sources: list[str], verdict: str) -> bool:
        return rating_policy.record_admin_wheel_decision(
            stats,
            wheel_key=wheel,
            sources=sources,
            decision=verdict,
            actor="admin",
            at=None,
            recorder=monitor_data.record_admin_wheel_decision,
        )

    assert decide("wheel-a", ["first", "second"], "confirmed") is True
    assert stats["sources"]["first"]["quality_score"] == 40
    assert stats["sources"]["second"]["quality_score"] == 40
    assert decide("wheel-a", ["first", "second"], "confirmed") is False

    assert decide("wheel-a", ["first", "second"], "inactive") is True
    assert stats["admin_wheel_decisions"]["wheel-a"]["decision"] == "inactive"
    for source in ("first", "second"):
        row = stats["sources"][source]
        assert row["quality_score"] == 0
        assert row["admin_rejected_wheels"] == 1
        assert "admin_confirmed_wheels" not in row
    assert decide("wheel-a", ["first", "second"], "inactive") is False


def _delivery_acceptance() -> None:
    original_path = notification_integrity_v2.STATE_PATH
    original_secret = os.environ.get("BOT_STATE_KEY")
    original_load = notification_router.load_config
    original_entries = dict(notification_integrity_v2._volatile_entries)
    try:
        with TemporaryDirectory() as temporary:
            notification_integrity_v2.STATE_PATH = (
                Path(temporary) / "notification_delivery_state.json"
            )
            notification_integrity_v2._volatile_entries.clear()
            os.environ["BOT_STATE_KEY"] = "chapter-2-acceptance-key"
            notification_integrity_v2.install(notification_router)
            notification_preferences_v2.install(notification_router)

            config = {
                "owner_id": "1",
                "admins": ["2", "4"],
                "blocked_users": ["4"],
                "settings": {"notifications": True},
                "users": {
                    "1": {"chat_id": "101", "notifications_enabled": True},
                    "2": {"chat_id": "202", "notifications_enabled": True},
                    "3": {
                        "chat_id": "303",
                        "notifications_enabled": True,
                        "notification_preferences": {"admin_system": True},
                    },
                    "4": {"chat_id": "404", "notifications_enabled": True},
                },
            }
            assert notification_router.recipients(
                config, True, "admin_system"
            ) == ["101", "202"]
            assert notification_router.recipients(config, True, "wheels") == [
                "101",
                "202",
                "303",
            ]

            notification_router.load_config = lambda: (config, True)

            class FakeMonitor:
                sent: list[dict[str, Any]] = []

                @classmethod
                def telegram_api(cls, method: str, payload: dict[str, Any]) -> dict:
                    assert method == "sendMessage"
                    cls.sent.append(dict(payload))
                    return {"ok": True, "result": {"message_id": len(cls.sent)}}

            notification_router.install(FakeMonitor)
            first = FakeMonitor.send_message(
                "🎡 <b>Новое колесо BetBoom</b>\n"
                "Идентификатор: <code>wheel-a</code>\n📡 @first",
                url="https://betboom.ru/freestream/wheel-a",
            )
            second = FakeMonitor.send_message(
                "🎡 <b>Новое колесо BetBoom</b>\n"
                "Идентификатор: <code>wheel-a</code>\n📡 @second",
                url="https://betboom.ru/freestream/wheel-a?source=second",
            )
            assert first["result"]["sent"] == 3
            assert second["result"]["sent"] == 0
            assert second["result"]["hidden_skipped"] == 3
            assert len(FakeMonitor.sent) == 3

            raw = notification_integrity_v2.STATE_PATH.read_text(encoding="utf-8")
            for private_value in ("chat_id", "user_id", "wheel-a", "@first"):
                assert private_value not in raw
            entries = notification_integrity_v2.load_state()["entries"]
            assert len(entries) == 3
            assert all(notification_integrity_v2.HEX_DIGEST_RE.fullmatch(key) for key in entries)
    finally:
        notification_integrity_v2.STATE_PATH = original_path
        notification_router.load_config = original_load
        notification_integrity_v2._volatile_entries.clear()
        notification_integrity_v2._volatile_entries.update(original_entries)
        if original_secret is None:
            os.environ.pop("BOT_STATE_KEY", None)
        else:
            os.environ["BOT_STATE_KEY"] = original_secret


def _publication_acceptance() -> None:
    rows = [
        {
            "source": "first",
            "message_id": 10,
            "message_date": "2026-07-15T09:00:00+00:00",
            "message_url": "https://telegram.me/first/10",
        },
        {
            "source": "second",
            "message_id": 20,
            "message_date": "2026-07-15T09:01:00+00:00",
            "message_url": "https://telegram.me/second/20",
        },
    ]
    merged = wheel_publications_v2.merge_publications([], rows)
    assert {row["source"] for row in merged} == {"first", "second"}

    state = {
        "active_wheels": {},
        "inactive_wheels": {},
        "recently_completed_wheels": {
            "wheel-a": {"removed_at": "2026-07-15T09:02:00+00:00"}
        },
        "wheel_publications": {"wheel-a": merged},
    }
    assert wheel_publications_v2.prune_closed_publications(state) == 1
    assert state["wheel_publications"] == {}


def self_test() -> None:
    _delivery_acceptance()
    _rating_acceptance()
    _publication_acceptance()
    print("chapter 2 unified notification, source and administrator logic passed")


if __name__ == "__main__":
    self_test()
