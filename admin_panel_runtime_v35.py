from __future__ import annotations

import argparse
from typing import Any

from admin_panel_runtime_v3 import INTERVAL_OPTIONS
from admin_panel_runtime_v34 import (
    TelegramPanelRuntimeV34,
    _clone,
    _merge_set_list,
    _merge_value,
)
from admin_panel_v2 import DEFAULT_SETTINGS, default_access


class TelegramPanelRuntimeV35(TelegramPanelRuntimeV34):
    """AES-GCM access state without the obsolete BOT_TOKEN role signature."""

    def normalize_access(self, value: dict[str, Any]) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        result = default_access()
        result["owner_id"] = str(raw.get("owner_id") or "")
        result["admins"] = sorted(
            {str(item) for item in raw.get("admins", []) if str(item)}
        )
        result["blocked_users"] = sorted(
            {str(item) for item in raw.get("blocked_users", []) if str(item)}
        )
        result["notification_recipients"] = sorted(
            {
                str(item)
                for item in raw.get("notification_recipients", [])
                if str(item)
            }
        )
        users = raw.get("users")
        result["users"] = _clone(users) if isinstance(users, dict) else {}

        raw_settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        settings = dict(raw_settings)
        settings.setdefault("public_panel", DEFAULT_SETTINGS["public_panel"])
        settings.setdefault(
            "notifications",
            raw_settings.get("wheel_notifications", DEFAULT_SETTINGS["notifications"]),
        )
        try:
            interval = int(raw_settings.get("monitor_interval_minutes", 5))
        except (TypeError, ValueError):
            interval = 5
        settings["monitor_interval_minutes"] = interval if interval in INTERVAL_OPTIONS else 5
        settings["public_panel"] = bool(settings["public_panel"])
        settings["notifications"] = bool(settings["notifications"])
        result["settings"] = settings

        owner_id = result["owner_id"]
        result["admins"] = [item for item in result["admins"] if item != owner_id]
        result["version"] = max(4, int(raw.get("version", 4) or 4))
        # AES-GCM authenticates the entire private state. The old access_signature
        # was tied to BOT_TOKEN and incorrectly cleared valid role changes.
        result.pop("access_signature", None)
        return result

    def _merge_access(
        self,
        base: dict[str, Any],
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> dict[str, Any]:
        base = self.normalize_access(base)
        local = self.normalize_access(local)
        remote = self.normalize_access(remote)
        result = _clone(remote)

        if local.get("owner_id") != base.get("owner_id"):
            result["owner_id"] = str(local.get("owner_id") or "")

        for key in ("admins", "blocked_users", "notification_recipients"):
            result[key] = _merge_set_list(base.get(key), local.get(key), remote.get(key))

        result["settings"] = _merge_value(
            base.get("settings", {}),
            local.get("settings", {}),
            remote.get("settings", {}),
        )

        base_users = base.get("users") if isinstance(base.get("users"), dict) else {}
        local_users = local.get("users") if isinstance(local.get("users"), dict) else {}
        remote_users = remote.get("users") if isinstance(remote.get("users"), dict) else {}
        merged_users = _clone(remote_users)

        for user_id in set(base_users) | set(local_users):
            base_record = (
                base_users.get(user_id) if isinstance(base_users.get(user_id), dict) else {}
            )
            if user_id not in local_users:
                if user_id in base_users:
                    merged_users.pop(user_id, None)
                continue
            local_record = (
                local_users.get(user_id) if isinstance(local_users.get(user_id), dict) else {}
            )
            if user_id in base_users and user_id not in remote_users and local_record == base_record:
                # Another process explicitly removed this user and the stale local
                # process did not change the record. Preserve the deletion.
                merged_users.pop(user_id, None)
                continue
            remote_record = (
                remote_users.get(user_id)
                if isinstance(remote_users.get(user_id), dict)
                else {}
            )
            merged_users[user_id] = _merge_value(base_record, local_record, remote_record)

        result["users"] = merged_users
        return self.normalize_access(result)

    def set_user_notification(
        self,
        user_id: str,
        key: str,
        enabled: bool | None = None,
    ) -> None:
        super().set_user_notification(user_id, key, enabled)
        self.dispatch("monitor.yml", {"continuous": "true"})

    def set_all_user_notifications(self, user_id: str, enabled: bool) -> None:
        super().set_all_user_notifications(user_id, enabled)
        self.dispatch("monitor.yml", {"continuous": "true"})


def self_test() -> None:
    panel = TelegramPanelRuntimeV35()
    base = panel.normalize_access(
        {
            "owner_id": "1",
            "admins": [],
            "blocked_users": [],
            "notification_recipients": ["10"],
            "settings": {"public_panel": True, "notifications": True},
            "users": {
                "1": {"id": "1", "chat_id": "10", "first_name": "Owner"},
                "2": {
                    "id": "2",
                    "chat_id": "20",
                    "first_name": "User",
                    "notification_preferences": {"wheels": True},
                },
            },
            "access_signature": "obsolete",
        }
    )
    assert "access_signature" not in base

    local = _clone(base)
    local["users"]["2"]["notification_preferences"]["wheel_final_reminders"] = False
    remote = _clone(base)
    remote["admins"] = ["2"]
    remote["users"]["2"]["last_name"] = "Remote"
    merged = panel._merge_access(base, local, remote)
    assert merged["admins"] == ["2"]
    assert merged["users"]["2"]["last_name"] == "Remote"
    assert merged["users"]["2"]["notification_preferences"]["wheel_final_reminders"] is False

    role_local = _clone(base)
    role_local["admins"] = ["2"]
    remote_with_new_user = _clone(base)
    remote_with_new_user["users"]["3"] = {"id": "3", "chat_id": "30"}
    merged_role = panel._merge_access(base, role_local, remote_with_new_user)
    assert merged_role["admins"] == ["2"]
    assert "3" in merged_role["users"]

    unchanged_local = _clone(base)
    remote_deleted = _clone(base)
    remote_deleted["users"].pop("2")
    merged_deleted = panel._merge_access(base, unchanged_local, remote_deleted)
    assert "2" not in merged_deleted["users"]

    changed_role = _clone(base)
    changed_role["admins"] = ["2"]
    normalized_role = panel.normalize_access(changed_role)
    assert normalized_role["admins"] == ["2"], "Valid role update was cleared"
    print("admin panel v35 role persistence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV35().run()


if __name__ == "__main__":
    raise SystemExit(main())
