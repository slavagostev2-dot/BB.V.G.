from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from typing import Any

import admin_bot as legacy
import admin_panel_v2

UTC = admin_panel_v2.UTC


class TelegramPanelRuntimeV2(admin_panel_v2.TelegramPanelV2):
    """Production wrapper with throttled access-file writes."""

    USER_SEEN_WRITE_INTERVAL = timedelta(hours=6)

    def register_user(self, message: dict[str, Any]) -> str:
        sender = message.get("from") if isinstance(message, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return "guest"

        user_id = str(sender.get("id") or "")
        chat_id = str(chat.get("id") or "")
        if not user_id:
            return "guest"

        access = self.load_access()
        changed = False
        if not access.get("owner_id"):
            bootstrap_ids = {str(legacy.BOT_CHAT_ID or ""), str(legacy.ADMIN_USER_ID or "")}
            if user_id in bootstrap_ids or chat_id in bootstrap_ids:
                access["owner_id"] = user_id
                if chat_id and chat_id not in access["notification_recipients"]:
                    access["notification_recipients"].append(chat_id)
                changed = True

        users = access.setdefault("users", {})
        previous = users.get(user_id, {}) if isinstance(users.get(user_id), dict) else {}
        now = datetime.now(UTC)
        last_seen = self.parse_dt(previous.get("last_seen_at"))
        update_seen = not last_seen or now - last_seen.astimezone(UTC) >= self.USER_SEEN_WRITE_INTERVAL

        record = dict(previous)
        profile = {
            "id": user_id,
            "chat_id": chat_id,
            "username": str(sender.get("username") or ""),
            "first_name": str(sender.get("first_name") or ""),
            "last_name": str(sender.get("last_name") or ""),
        }
        if not record:
            record["first_seen_at"] = now.isoformat()
            update_seen = True
        for key, value in profile.items():
            if record.get(key) != value:
                record[key] = value
                changed = True
        if update_seen:
            record["last_seen_at"] = now.isoformat()
            changed = True

        if users.get(user_id) != record:
            users[user_id] = record
        if changed:
            self.save_access("Update Telegram panel user [skip ci]")
        return self.role_for(user_id)


def self_test() -> None:
    admin_panel_v2.self_test()
    assert TelegramPanelRuntimeV2.USER_SEEN_WRITE_INTERVAL == timedelta(hours=6)
    print("admin_panel_runtime_v2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV2().run()


if __name__ == "__main__":
    raise SystemExit(main())
