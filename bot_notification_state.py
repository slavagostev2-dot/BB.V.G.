from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import bot_private_state
import notification_integrity_v2
import notification_remote_checkpoint
import notification_router

notification_integrity_v2.install(notification_router)
notification_remote_checkpoint.install(notification_router, notification_integrity_v2)

if "bbvg.bot.runtime" in sys.modules:
    import admin_bot as legacy_admin
    import personal_wheel_voting
    from bbvg.bot import natural_language_admin
    from bbvg.bot import profile as hunter_profile
    from bbvg.bot.users import UserManagementRuntime

    _previous_profile_handler = personal_wheel_voting.PersonalWheelVotingMixin.handle_callback
    hunter_profile.install(personal_wheel_voting.PersonalWheelVotingMixin)
    _new_profile_handler = personal_wheel_voting.PersonalWheelVotingMixin.handle_callback

    def _combined_profile_handler(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data in {"page:profile", "profile:refresh"}:
            _new_profile_handler(self, query)
            return
        _previous_profile_handler(self, query)

    personal_wheel_voting.PersonalWheelVotingMixin.handle_callback = _combined_profile_handler

    if "compact_menu_rows" in personal_wheel_voting.PersonalWheelVotingMixin.__dict__:
        delattr(personal_wheel_voting.PersonalWheelVotingMixin, "compact_menu_rows")
    if not getattr(UserManagementRuntime, "_bbvg_hunter_profile_menu_installed", False):
        _base_compact_menu_rows = UserManagementRuntime.compact_menu_rows

        def _compact_menu_rows_with_profile(admin: bool) -> list[list[dict[str, Any]]]:
            rows = [list(row) for row in _base_compact_menu_rows(admin)]
            rows.append([{"text": "👤 Мой профиль", "callback_data": "page:profile"}])
            return rows

        UserManagementRuntime.compact_menu_rows = staticmethod(_compact_menu_rows_with_profile)
        UserManagementRuntime._bbvg_hunter_profile_menu_installed = True

    natural_language_admin.install(legacy_admin.AdminBot)


FAST_MONITOR_INTERVAL_MINUTES = 1


def _with_fast_monitor_interval(access: dict[str, Any]) -> dict[str, Any]:
    settings = access.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        access["settings"] = settings
    settings["monitor_interval_minutes"] = FAST_MONITOR_INTERVAL_MINUTES
    return access


def load_config() -> tuple[dict[str, Any], bool]:
    bundle = bot_private_state.load_file(
        access_default={},
        source_requests_default={"version": 1, "requests": {}},
    )
    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    exists = bool(access.get("owner_id") or access.get("users"))
    if exists:
        return _with_fast_monitor_interval(access), True
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
        "settings": {
            "notifications": True,
            "public_panel": True,
            "monitor_interval_minutes": FAST_MONITOR_INTERVAL_MINUTES,
        },
    }, True


def admin_recipients() -> list[str]:
    access, exists = load_config()
    if not exists:
        return []
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    blocked = {str(value) for value in access.get("blocked_users", []) if str(value)}
    admin_ids = {
        str(value)
        for value in [access.get("owner_id"), *access.get("admins", [])]
        if str(value or "") and str(value) not in blocked
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
        with TemporaryDirectory() as temporary:
            bot_private_state.STATE_PATH = Path(temporary) / "missing-state.enc.json"
            access, exists = load_config()
            assert isinstance(access, dict)
            assert isinstance(exists, bool)
            assert notification_router._bbvg_notification_integrity_v2_installed is True
            assert notification_router._bbvg_remote_notification_checkpoint_installed is True
    finally:
        bot_private_state.STATE_PATH = original
    print("BB V.G. bot notification state self-test passed")


if __name__ == "__main__":
    self_test()
