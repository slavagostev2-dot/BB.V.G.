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

        now = datetime.now(UTC)
        for attempt in range(3):
            # A retry always starts from the newest repository version so a
            # simultaneous monitor commit cannot erase the registration.
            access = self.load_access(force=attempt > 0)
            changed = False
            if not access.get("owner_id"):
                bootstrap_ids = {
                    str(legacy.BOT_CHAT_ID or ""),
                    str(legacy.ADMIN_USER_ID or ""),
                }
                if user_id in bootstrap_ids or chat_id in bootstrap_ids:
                    access["owner_id"] = user_id
                    if chat_id and chat_id not in access["notification_recipients"]:
                        access["notification_recipients"].append(chat_id)
                    changed = True

            users = access.setdefault("users", {})
            previous = users.get(user_id, {}) if isinstance(users.get(user_id), dict) else {}
            last_seen = self.parse_dt(previous.get("last_seen_at"))
            update_seen = (
                not last_seen
                or now - last_seen.astimezone(UTC) >= self.USER_SEEN_WRITE_INTERVAL
            )

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
            if not changed:
                return self.role_for(user_id)
            try:
                self.save_access("Update Telegram panel user [skip ci]")
            except RuntimeError as exc:
                if attempt >= 2:
                    raise
                self.access_loaded = False
                print(
                    f"WARNING user registration retry {attempt + 1}/3: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            return self.role_for(user_id)
        return self.role_for(user_id)


def self_test() -> None:
    admin_panel_v2.self_test()
    assert TelegramPanelRuntimeV2.USER_SEEN_WRITE_INTERVAL == timedelta(hours=6)
    bot = TelegramPanelRuntimeV2()
    persisted = admin_panel_v2.default_access()
    save_attempts: list[int] = []

    def load_access(force: bool = False) -> dict[str, Any]:
        if force or not bot.access_loaded:
            bot.access = {
                **persisted,
                "settings": dict(persisted["settings"]),
                "users": {key: dict(value) for key, value in persisted["users"].items()},
            }
            bot.access_loaded = True
        return bot.access

    def save_access(message: str = "") -> None:
        save_attempts.append(1)
        if len(save_attempts) == 1:
            raise RuntimeError("simulated GitHub conflict")
        persisted.clear()
        persisted.update(bot.access)

    bot.load_access = load_access  # type: ignore[method-assign]
    bot.save_access = save_access  # type: ignore[method-assign]
    bot.role_for = lambda user_id: "user"  # type: ignore[method-assign]
    role = bot.register_user({
        "chat": {"id": 303, "type": "private"},
        "from": {"id": 303, "first_name": "Тест", "username": "test_user"},
    })
    assert role == "user"
    assert len(save_attempts) == 2
    assert bot.access["users"]["303"]["username"] == "test_user"
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
