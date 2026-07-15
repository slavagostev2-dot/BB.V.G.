from __future__ import annotations

import os
from datetime import datetime, timezone

import bot_private_state

UTC = timezone.utc


def build_fixture() -> dict:
    now = datetime.now(UTC).isoformat()
    access = {
        "version": 3,
        "owner_id": "1",
        "admins": [],
        "users": {
            "1": {
                "id": "1",
                "chat_id": "1",
                "username": "test_owner",
                "first_name": "Test",
                "last_name": "Owner",
                "first_seen_at": now,
                "last_seen_at": now,
                "notifications_enabled": True,
                "notification_preferences": {
                    "wheels": True,
                    "wheel_final_reminders": True,
                    "wheel_draw_alerts": False,
                    "admin_system": True,
                    "admin_sources": True,
                    "admin_requests": True,
                },
                "participating_wheels": {},
                "hidden_wheels": {},
            },
            "2": {
                "id": "2",
                "chat_id": "2",
                "username": "test_user",
                "first_name": "Test",
                "last_name": "User",
                "first_seen_at": now,
                "last_seen_at": now,
                "notifications_enabled": True,
                "notification_preferences": {
                    "wheels": True,
                    "wheel_final_reminders": True,
                    "wheel_draw_alerts": False,
                    "admin_system": False,
                    "admin_sources": False,
                    "admin_requests": False,
                },
                "participating_wheels": {},
                "hidden_wheels": {},
            },
        },
        "blocked_users": [],
        "notification_recipients": ["1", "2"],
        "settings": {
            "public_panel": True,
            "notifications": True,
            "wheel_notifications": True,
            "daily_reports": False,
            "weekly_reports": False,
            "notification_policy_version": 1,
            "monitor_interval_minutes": 5,
        },
    }
    return bot_private_state.default_bundle(
        access,
        {"version": 1, "requests": {}},
    )


def install() -> None:
    if os.getenv("BBVG_TEST_MODE") != "1":
        raise RuntimeError("Refusing to replace bot state outside BBVG_TEST_MODE=1")
    if not os.getenv("BOT_STATE_KEY"):
        raise RuntimeError("BOT_STATE_KEY is required for the encrypted test fixture")
    value = build_fixture()
    bot_private_state.save_file(value)
    restored = bot_private_state.load_file()
    if restored != value:
        raise AssertionError("Encrypted test fixture did not round-trip")
    print("Encrypted bot state CI fixture installed")


if __name__ == "__main__":
    install()
