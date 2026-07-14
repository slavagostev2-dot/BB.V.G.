from __future__ import annotations

import os
from typing import Any

import bot_private_state


def load_config() -> tuple[dict[str, Any], bool]:
    bundle = bot_private_state.load_file(
        access_default={},
        source_requests_default={"version": 1, "requests": {}},
    )
    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    exists = bool(access.get("owner_id") or access.get("users"))
    if exists:
        return access, True
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if not fallback:
        return {}, False
    return {
        "version": 3,
        "owner_id": fallback,
        "admins": [],
        "users": {
            fallback: {
                "id": fallback,
                "chat_id": fallback,
                "notifications_enabled": True,
            }
        },
        "notification_recipients": [fallback],
        "blocked_users": [],
        "settings": {"notifications": True, "public_panel": True},
    }, True


def admin_recipients() -> list[str]:
    access, exists = load_config()
    if not exists:
        return []
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    admin_ids = {
        str(value)
        for value in [access.get("owner_id"), *access.get("admins", [])]
        if str(value or "")
    }
    result = {
        str((users.get(user_id) or {}).get("chat_id") or user_id)
        for user_id in admin_ids
        if isinstance(users.get(user_id), dict)
    }
    if result:
        return sorted(value for value in result if value)
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    return [fallback] if fallback else []


def self_test() -> None:
    original = bot_private_state.STATE_PATH
    try:
        access, exists = load_config()
        assert isinstance(access, dict)
        assert isinstance(exists, bool)
    finally:
        bot_private_state.STATE_PATH = original
    print("BB V.G. bot notification state self-test passed")


if __name__ == "__main__":
    self_test()
