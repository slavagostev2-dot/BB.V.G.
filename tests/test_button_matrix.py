from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import notification_router
from admin_panel_runtime_v38 import TelegramPanelRuntimeV38


def callbacks(rows: list[list[dict[str, Any]]]) -> set[str]:
    return {
        str(button.get("callback_data") or "")
        for row in rows
        for button in row
        if button.get("callback_data")
    }


class ButtonMatrixTests(unittest.TestCase):
    def panel(self, *, admin: bool) -> tuple[TelegramPanelRuntimeV38, list[Any]]:
        panel = TelegramPanelRuntimeV38()
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
        panel.show_active = lambda: calls.append(("show_active",))  # type: ignore[method-assign]
        panel.refresh_snapshot = lambda: calls.append(("refresh",))  # type: ignore[method-assign]
        panel.mark_personal_participation = lambda key: calls.append(("personal", key))  # type: ignore[method-assign]
        panel.hide_wheel_for_current_user = lambda key: calls.append(("hide", key))  # type: ignore[method-assign]
        panel.request_manual_time = lambda key: calls.append(("time", key))  # type: ignore[method-assign]
        panel.dispatch_admin_action = lambda action, value: calls.append(  # type: ignore[method-assign]
            ("admin", action, value)
        ) or {"queued": True, "detail": "queued"}
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
        user = callbacks(TelegramPanelRuntimeV38.compact_menu_rows(False))
        admin = callbacks(TelegramPanelRuntimeV38.compact_menu_rows(True))
        self.assertNotIn("page:control", user)
        self.assertIn("page:status", user)
        self.assertIn("page:control", admin)
        self.assertNotIn("page:status", admin)
        self.assertEqual(len(user), sum(len(row) for row in TelegramPanelRuntimeV38.compact_menu_rows(False)))
        self.assertEqual(len(admin), sum(len(row) for row in TelegramPanelRuntimeV38.compact_menu_rows(True)))

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
        self.assertIn("✅ Я участвую", str(user))
        self.assertIn("Скрыть у меня", str(user))
        self.assertNotIn("wheel:time:one", str(user))
        self.assertIn("✅ Участвую", str(admin))
        self.assertIn("wheel:time:one", str(admin))

    def test_user_callbacks_change_only_personal_state(self) -> None:
        panel, calls = self.panel(admin=False)
        panel.handle_callback(self.query("wheel:part:wheel-a", 3))
        panel.handle_callback(self.query("wheel:inactive:wheel-a", 3))
        panel.handle_callback(self.query("wheel:time:wheel-a", 3))
        panel.handle_callback(self.query("wheel:finished:wheel-a", 3))
        self.assertIn(("personal", "wheel-a"), calls)
        self.assertIn(("hide", "wheel-a"), calls)
        self.assertFalse(any(row[0] == "admin" for row in calls))
        self.assertFalse(any(row[0] == "time" for row in calls))

    def test_admin_callbacks_queue_every_global_decision(self) -> None:
        panel, calls = self.panel(admin=True)
        panel.handle_callback(self.query("wheel:part:wheel-a"))
        panel.handle_callback(self.query("wheel:inactive:wheel-a"))
        panel.handle_callback(self.query("wheel:time:wheel-a"))
        panel.handle_callback(self.query("wheel:finished:wheel-a"))
        admin_calls = [row for row in calls if row[0] == "admin"]
        self.assertTrue(any(row[1] == "participate_wheel" for row in admin_calls))
        self.assertTrue(any(row[1] == "mark_inactive_global" for row in admin_calls))
        self.assertTrue(any(row[1] == "confirm_finished_global" for row in admin_calls))
        self.assertIn(("time", "wheel-a"), calls)

    def test_active_list_renders_role_specific_controls(self) -> None:
        for admin in (False, True):
            panel = TelegramPanelRuntimeV38()
            captured: list[dict[str, Any]] = []
            panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
                {
                    "_key": "wheel-a",
                    "identifier": "wheel-a",
                    "source": "mechanogun",
                    "url": "https://betboom.ru/freestream/wheel-a",
                }
            ]
            panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
                state={"active_wheels": {}}, stats={}, health={}, discovery={}, fast=[], nightly=[]
            )
            panel._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
            panel._monitor_status = lambda: {}  # type: ignore[method-assign]
            panel._sources_for_item = lambda snap, key, item: ["mechanogun"]  # type: ignore[method-assign]
            panel.is_admin = lambda: admin  # type: ignore[method-assign]
            panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
            panel.send = lambda text, **kwargs: captured.append(kwargs["reply_markup"]) or {}  # type: ignore[method-assign]
            panel.show_active()
            markup = str(captured[-1])
            self.assertIn("wheel:part:wheel-a", markup)
            self.assertIn("wheel:inactive:wheel-a", markup)
            if admin:
                self.assertIn("wheel:finished:wheel-a", markup)
                self.assertIn("wheel:time:wheel-a", markup)
            else:
                self.assertNotIn("wheel:finished:wheel-a", markup)
                self.assertNotIn("wheel:time:wheel-a", markup)


if __name__ == "__main__":
    unittest.main()
