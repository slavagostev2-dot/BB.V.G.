from __future__ import annotations

import argparse
from typing import Any

from admin_panel_runtime_v34 import TelegramPanelRuntimeV34
from bbvg.bot.storage import self_test as storage_self_test


class TelegramPanelRuntimeV35(TelegramPanelRuntimeV34):
    """Refresh monitor preferences after owner-managed notification changes."""

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
    storage_self_test()
    panel = TelegramPanelRuntimeV35()
    base = panel.normalize_access(
        {
            "owner_id": "1",
            "admins": [],
            "blocked_users": [],
            "notification_recipients": ["10", "20"],
            "settings": {"public_panel": True, "notifications": True},
            "users": {
                "1": {"id": "1", "chat_id": "10"},
                "2": {
                    "id": "2",
                    "chat_id": "20",
                    "notification_preferences": {"wheels": True},
                },
            },
            "access_signature": "obsolete",
        }
    )
    assert "access_signature" not in base

    access: dict[str, Any] = base
    saved: list[str] = []
    dispatched: list[tuple[str, dict[str, str]]] = []
    panel.current_user_id = "1"
    panel.current_role = "owner"
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.load_access = lambda force=False: access  # type: ignore[method-assign]
    panel.save_access = lambda message="": saved.append(message)  # type: ignore[method-assign]
    panel.role_for = lambda user_id: "owner" if str(user_id) == "1" else "user"  # type: ignore[method-assign]
    panel.notification_preferences = lambda user_id=None: {  # type: ignore[method-assign]
        "wheels": bool(
            access["users"].get(str(user_id or panel.current_user_id), {})
            .get("notification_preferences", {})
            .get("wheels", True)
        ),
        "wheel_final_reminders": True,
        "wheel_draw_alerts": False,
        "daily_reports": False,
        "weekly_reports": False,
        "admin_system": False,
        "admin_sources": False,
        "admin_requests": False,
    }
    panel.dispatch = lambda workflow, inputs=None: dispatched.append(  # type: ignore[method-assign]
        (workflow, dict(inputs or {}))
    )
    panel.set_user_notification("2", "wheels", False)
    assert saved
    assert dispatched == [("monitor.yml", {"continuous": "true"})]
    print("admin panel v35 notification refresh self-test passed")


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
