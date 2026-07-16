from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import notification_router
import personal_wheel_voting
from bbvg.bot.runtime import TelegramPanelRuntime

personal_wheel_voting.install_notification_router(notification_router)


def callbacks(rows: list[list[dict[str, Any]]]) -> set[str]:
    return {
        str(button.get("callback_data") or "")
        for row in rows
        for button in row
        if button.get("callback_data")
    }


class ButtonMatrixTests(unittest.TestCase):
    def panel(self, *, admin: bool) -> tuple[TelegramPanelRuntime, list[Any]]:
        panel = TelegramPanelRuntime()
        calls: list[Any] = []
        panel.current_user_id = "1" if admin else "3"
        panel.current_chat_id = panel.current_user_id
        panel.current_role = "owner" if admin else "user"
        panel.set_context = lambda chat_id, user_id: None  # type: ignore[method-assign]
        panel._prepare_callback_user = lambda query: None  # type: ignore[method-assign]
        panel.is_admin = lambda: admin  # type: ignore[method-assign]
        panel.answer = lambda query_id, text="": calls.append(("answer", text))  # type: ignore[method-assign]
        panel.send = lambda text, **kwargs: calls.append(("send", text)) or {}  # type: ignore[method-assign]
        panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
        panel.show_active = lambda page=0: calls.append(("show_active", page))  # type: ignore[method-assign]
        panel.refresh_snapshot = lambda: calls.append(("refresh",))  # type: ignore[method-assign]
        panel.mark_personal_participation = lambda key: calls.append(("personal", key)) or {"changed": True}  # type: ignore[method-assign]
        panel.request_manual_time = lambda key: calls.append(("time", key))  # type: ignore[method-assign]
        panel.dispatch_admin_action = lambda action, value: calls.append(  # type: ignore[method-assign]
            ("admin", action, value)
        ) or {"queued": True, "detail": "queued"}
        panel._resolve_wheel_token = lambda token: token  # type: ignore[method-assign]
        return panel, calls

    @staticmethod
    def query(data: str, user_id: int = 1) -> dict[str, Any]:
        return {
            "id": f"query-{data}",
            "data": data,
            "from": {"id": user_id},
            "message": {"message_id": 1, "chat": {"id": user_id}},
        }

    def test_main_menu_access_matrix_has_no_duplicate_actions(self) -> None:
        user = callbacks(TelegramPanelRuntime.compact_menu_rows(False))
        admin = callbacks(TelegramPanelRuntime.compact_menu_rows(True))
        self.assertNotIn("page:control", user)
        self.assertIn("page:status", user)
        self.assertIn("page:control", admin)
        self.assertNotIn("page:status", admin)
        self.assertEqual(len(user), sum(len(row) for row in TelegramPanelRuntime.compact_menu_rows(False)))
        self.assertEqual(len(admin), sum(len(row) for row in TelegramPanelRuntime.compact_menu_rows(True)))

    def test_notification_button_role_matrix(self) -> None:
        source = {
            "inline_keyboard": [
                [
                    {"text": "Участие", "callback_data": "wheel:part:one"},
                    {"text": "Неактивное", "callback_data": "wheel:inactive:one"},
                ],
                [{"text": "Время", "callback_data": "wheel:time:one"}],
            ]
        }
        user = notification_router.markup_for_chat(source, admin=False)
        admin = notification_router.markup_for_chat(source, admin=True)
        self.assertIn("✅ Участвую", str(user))
        self.assertNotIn("Скрыть у меня", str(user))
        self.assertNotIn("wheel:inactive:one", str(user))
        self.assertNotIn("wheel:time:one", str(user))
        self.assertIn("✅ Участвую", str(admin))
        self.assertNotIn("wheel:inactive:one", str(admin))
        self.assertIn("wheel:time:one", str(admin))

    def test_participation_is_personal_for_every_role(self) -> None:
        for admin, user_id in ((False, 3), (True, 1)):
            panel, calls = self.panel(admin=admin)
            panel.handle_callback(self.query("wheel:part:wheel-a", user_id))
            self.assertIn(("personal", "wheel-a"), calls)
            self.assertFalse(any(row[0] == "admin" for row in calls))

    def test_users_and_admins_cannot_delete_in_api_mode(self) -> None:
        for admin, user_id in ((False, 3), (True, 1)):
            panel, calls = self.panel(admin=admin)
            panel.handle_callback(self.query("wheel:inactive:wheel-a", user_id))
            panel.handle_callback(self.query("wheel:finished:wheel-a", user_id))
            self.assertFalse(any(row[0] == "admin" for row in calls))
            answers = [row[1] for row in calls if row[0] == "answer"]
            self.assertTrue(any("BetBoom API" in value for value in answers))

    def test_active_list_renders_only_personal_and_time_controls(self) -> None:
        for admin in (False, True):
            panel = TelegramPanelRuntime()
            captured: list[dict[str, Any]] = []
            panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
                {
                    "_key": "wheel-a",
                    "identifier": "wheel-a",
                    "source": "mechanogun",
                    "sources": ["mechanogun", "second"],
                    "action_id": 10,
                    "url": "https://betboom.ru/freestream/wheel-a",
                }
            ]
            panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
                state={"active_wheels": {}}, stats={}, health={}, discovery={}, fast=[], nightly=[]
            )
            panel._personal_participating_wheels = lambda: set()  # type: ignore[method-assign]
            panel._monitor_status = lambda: {}  # type: ignore[method-assign]
            panel._sources_for_item = lambda snap, key, item: ["mechanogun", "second"]  # type: ignore[method-assign]
            panel.is_admin = lambda: admin  # type: ignore[method-assign]
            panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
            panel.send = lambda text, **kwargs: captured.append({"text": text, **kwargs}) or {}  # type: ignore[method-assign]
            panel.show_active()
            payload = captured[-1]
            markup = str(payload["reply_markup"])
            self.assertIn("@mechanogun, @second", payload["text"])
            self.assertIn("wheel:part:wheel-a", markup)
            self.assertNotIn("wheel:inactive:wheel-a", markup)
            self.assertNotIn("wheel:finished:wheel-a", markup)
            if admin:
                self.assertIn("wheel:time:wheel-a", markup)
            else:
                self.assertNotIn("wheel:time:wheel-a", markup)


if __name__ == "__main__":
    unittest.main()
