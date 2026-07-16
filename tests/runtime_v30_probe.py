from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bot_private_state
from bbvg.bot.source_requests import default_source_requests
from admin_panel_runtime_v30 import TelegramPanelRuntimeV30


def snapshot() -> tuple[str, dict, dict]:
    cls = TelegramPanelRuntimeV30
    original_load = cls.load_access
    original_path = bot_private_state.STATE_PATH

    def local(self, force: bool = False):
        bundle = getattr(self, "_bot_bundle", None)
        if isinstance(bundle, dict):
            self.access = self.normalize_access(bundle.get("access", {}))
            self.access_loaded = True
            return self.access
        return original_load(self, False)

    cls.load_access = local
    try:
        with TemporaryDirectory() as directory:
            bot_private_state.STATE_PATH = Path(directory) / "state.json"
            panel = cls()
            access = panel._bootstrap_access(
                {
                    "owner_id": "1",
                    "users": {
                        "1": {
                            "id": "1",
                            "chat_id": "1",
                            "first_name": "Owner",
                            "notifications_enabled": True,
                        }
                    },
                }
            )
            panel._bot_bundle = bot_private_state.default_bundle(
                access, default_source_requests()
            )
            panel._save_bot_bundle = lambda message: True
            panel.send = lambda *args, **kwargs: {"ok": True}
            role = panel.register_user(
                {
                    "chat": {"id": 2, "type": "private"},
                    "from": {
                        "id": 2,
                        "username": "new_user",
                        "first_name": "New",
                    },
                }
            )
            record = dict(panel.access.get("users", {}).get("2") or {})
            return role, record, dict(panel.access)
    finally:
        cls.load_access = original_load
        bot_private_state.STATE_PATH = original_path


def main() -> int:
    probe = sys.argv[1]
    role, record, access = snapshot()
    checks = {
        "role": role == "user",
        "record": bool(record),
        "enabled": record.get("notifications_enabled") is True,
        "wheels": record.get("notification_preferences", {}).get("wheels") is True,
        "daily": record.get("notification_preferences", {}).get("daily_reports") is False,
        "recipient": "2" in {str(value) for value in access.get("notification_recipients", [])},
    }
    assert checks[probe], {"probe": probe, "role": role, "record": record, "access": access}
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
