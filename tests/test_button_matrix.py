from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
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
        panel.navigation = {panel.current_user_id: ["menu"]}
        panel.set_context = lambda chat_id, user_id: None  # type: ignore[method-assign]
        panel._prepare_callback_user = lambda query: None  # type: ignore[method-assign]
        panel.is_admin = lambda: admin  # type: ignore[method-assign]
        panel.role_for = lambda user_id: "owner" if admin else "user"  # type: ignore[method-assign]
        panel.role_name = lambda role: "Владелец" if admin else "Пользователь"  # type: ignore[method-assign]
        panel.answer = lambda query_id, text="": calls.append(("answer", text))  # type: ignore[method-assign]
        panel.send = lambda text, **kwargs: calls.append(  # type: ignore[method-assign]
            ("send", text, getattr(panel, "_edit_message_id", None), kwargs)
        ) or {}
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

    def test_analytics_buttons_keep_pre_update_order(self) -> None:
        for admin in (False, True):
            panel, calls = self.panel(admin=admin)
            panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
                stats={"sources": {}}
            )
            panel.period_overview = lambda snap, days: {  # type: ignore[method-assign]
                "wheel_posts": 0,
                "sources_with_wheels": 0,
                "notifications": 0,
                "top_sources": [],
                "best_day": None,
                "active": 0,
                "active_with_time": 0,
                "participating": 0,
            }
            panel.period_totals = lambda stats, days: {}  # type: ignore[method-assign]
            panel._collect_current_wheels = lambda: []  # type: ignore[method-assign]
            panel._registry_snapshot = lambda snap: {"summary": {}}  # type: ignore[method-assign]

            panel.show_analytics(7)

            rows = calls[-1][3]["reply_markup"]["inline_keyboard"]
            callback_rows = [
                [str(button.get("callback_data") or "") for button in row]
                for row in rows
            ]
            expected = [[
                "page:analytics:1",
                "page:analytics:7",
                "page:analytics:30",
            ]]
            if admin:
                expected.append(["page:report:inactive"])
            self.assertEqual(callback_rows, expected)

    def test_finished_wheel_is_not_rendered_during_monitor_refresh_delay(self) -> None:
        panel, _ = self.panel(admin=False)
        now = datetime.now(timezone.utc)
        panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
            state={
                "active_wheels": {
                    "finished": {
                        "identifier": "finished",
                        "deadline": (now - timedelta(seconds=1)).isoformat(),
                    },
                    "future": {
                        "identifier": "future",
                        "deadline": (now + timedelta(minutes=5)).isoformat(),
                    },
                },
                "inactive_wheels": {},
            }
        )
        panel._hidden_wheels = lambda user_id=None: {}  # type: ignore[method-assign]

        items = panel._collect_current_wheels()

        self.assertEqual([item["_key"] for item in items], ["future"])

    def test_source_buttons_keep_pre_update_order_and_actions(self) -> None:
        expected = {
            False: [
                ["page:sources", "page:ranking"],
                ["source_list:primary:0", "source:request"],
            ],
            True: [
                ["page:sources", "page:ranking"],
                ["source_list:primary:0", "page:discovery"],
                ["page:intelligence", "source:add"],
            ],
        }
        for admin in (False, True):
            panel, calls = self.panel(admin=admin)
            panel.snapshot = lambda force=False: SimpleNamespace()  # type: ignore[method-assign]
            panel._registry_snapshot = lambda snap: {  # type: ignore[method-assign]
                "summary": {
                    "total": 3,
                    "primary": 1,
                    "nightly": 1,
                    "checked": 2,
                    "available": 2,
                    "unavailable": 0,
                    "pending": 0,
                }
            }
            panel.source_sets = lambda snap: {  # type: ignore[method-assign]
                "primary": ["main"],
                "reserve": ["nightly"],
                "paused": ["paused"],
            }

            panel.show_sources()

            rows = calls[-1][3]["reply_markup"]["inline_keyboard"]
            callback_rows = [
                [str(button.get("callback_data") or "") for button in row]
                for row in rows
            ]
            self.assertEqual(callback_rows, expected[admin])

    def test_source_intelligence_overview_findings_and_detail_are_routable(self) -> None:
        panel, calls = self.panel(admin=True)
        candidate = {
            "source": "candidate",
            "decision": "new",
            "score": 75,
            "public": True,
            "mention_count": 4,
            "messages_checked": 20,
            "wheel_links_found": 2,
            "discovered_from": ["known"],
            "last_verified_at": "2026-07-18T08:00:00+00:00",
        }
        panel.intelligence_state = lambda: {  # type: ignore[method-assign]
            "last_run_at": "2026-07-18T08:00:00+00:00",
            "last_run_summary": {"sources_scanned": 81},
        }
        panel.intelligence_rows = lambda: [candidate]  # type: ignore[method-assign]
        panel.workflow_run = lambda name: {  # type: ignore[method-assign]
            "status": "completed",
            "conclusion": "success",
        }

        panel.handle_callback(self.query("page:intelligence"))
        overview = [row for row in calls if row[0] == "send"][-1]
        self.assertIn("Разведка новых источников", overview[1])
        self.assertIn("Новых кандидатов: <b>1</b>", overview[1])
        self.assertIn("intel:list:new:0", str(overview[3]["reply_markup"]))

        panel.handle_callback(self.query("intel:list:new:0"))
        findings = [row for row in calls if row[0] == "send"][-1]
        self.assertIn("Новые источники из Telegram-сети", findings[1])
        self.assertIn("@candidate", findings[1])
        self.assertIn("intel:detail:candidate", str(findings[3]["reply_markup"]))

        panel.handle_callback(self.query("intel:detail:candidate"))
        detail = [row for row in calls if row[0] == "send"][-1]
        self.assertIn("@candidate", detail[1])
        self.assertIn("Найдено колёс: 2", detail[1])

    def test_source_intelligence_is_role_safe_for_forged_page_callback(self) -> None:
        panel, calls = self.panel(admin=False)
        panel.handle_callback(self.query("page:intelligence", user_id=3))
        sent = [row for row in calls if row[0] == "send"]
        self.assertTrue(sent)
        self.assertIn("доступен администраторам", sent[-1][1])
        self.assertNotIn("Выберите раздел", sent[-1][1])

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
        _, final_admin = TelegramPanelRuntime._simplify_active_payload("", admin)
        self.assertIn("✅ Участвую", str(user))
        self.assertNotIn("Скрыть у меня", str(user))
        self.assertNotIn("wheel:inactive:one", str(user))
        self.assertNotIn("wheel:time:one", str(user))
        self.assertIn("✅ Участвую", str(final_admin))
        self.assertNotIn("wheel:inactive:one", str(final_admin))
        self.assertNotIn("wheel:time:one", str(final_admin))

    def test_participation_is_personal_and_opens_menu_in_same_message(self) -> None:
        for admin, user_id in ((False, 3), (True, 1)):
            panel, calls = self.panel(admin=admin)
            panel.handle_callback(self.query("wheel:part:wheel-a", user_id))
            self.assertIn(("personal", "wheel-a"), calls)
            self.assertFalse(any(row[0] == "admin" for row in calls))
            menu_updates = [
                row for row in calls
                if row[0] == "send" and "Выберите раздел" in row[1]
            ]
            self.assertEqual(len(menu_updates), 1)
            self.assertEqual(menu_updates[0][2], 1)
            self.assertFalse(any(row[0] == "show_active" for row in calls))

    def test_users_and_admins_cannot_delete_or_set_time_in_api_mode(self) -> None:
        for admin, user_id in ((False, 3), (True, 1)):
            panel, calls = self.panel(admin=admin)
            panel.handle_callback(self.query("wheel:inactive:wheel-a", user_id))
            panel.handle_callback(self.query("wheel:finished:wheel-a", user_id))
            panel.handle_callback(self.query("wheel:time:wheel-a", user_id))
            self.assertFalse(any(row[0] == "admin" for row in calls))
            self.assertFalse(any(row[0] == "time" for row in calls))
            answers = [row[1] for row in calls if row[0] == "answer"]
            self.assertTrue(any("BetBoom API" in value for value in answers))
            self.assertTrue(any("указание времени отключено" in value for value in answers))

    def test_active_list_renders_only_open_and_personal_controls(self) -> None:
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
            self.assertNotIn("wheel:time:wheel-a", markup)


if __name__ == "__main__":
    unittest.main()
