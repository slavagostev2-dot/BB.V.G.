from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import telegram_ui
from admin_panel_runtime_v38 import (
    ACCESS_PAGE_SIZE,
    ACTIVE_PAGE_SIZE,
    TelegramPanelRuntimeV38,
)


UTC = timezone.utc


def flat_buttons(markup: dict[str, Any]) -> list[dict[str, Any]]:
    return [button for row in markup.get("inline_keyboard", []) for button in row]


class Chapter4InterfaceTests(unittest.TestCase):
    @staticmethod
    def snapshot(**overrides: Any) -> SimpleNamespace:
        values = {
            "state": {"active_wheels": {}, "participating_wheels": {}},
            "stats": {"sources": {}, "daily": {}},
            "health": {"sources": {}},
            "discovery": {"sources": {}},
            "fast": [],
            "nightly": [],
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def capture_panel(*, role: str = "user") -> tuple[TelegramPanelRuntimeV38, list[Any]]:
        panel = TelegramPanelRuntimeV38()
        captured: list[Any] = []
        panel.current_user_id = "1"
        panel.current_chat_id = "1"
        panel.current_role = role
        panel.navigation = {"1": ["menu"]}
        panel.is_admin = lambda: role in {"owner", "admin"}  # type: ignore[method-assign]
        panel.is_owner = lambda: role == "owner"  # type: ignore[method-assign]
        panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
        return panel, captured

    def test_html_is_cut_without_broken_markup(self) -> None:
        value = "<b>Экран</b>\n<code>" + ("длинная строка &amp; " * 800) + "</code>"
        clipped = telegram_ui.truncate_telegram_html(value)
        self.assertLessEqual(len(clipped), telegram_ui.TELEGRAM_TEXT_LIMIT)
        self.assertTrue(clipped.endswith("…"))
        self.assertEqual(clipped.count("<code>"), clipped.count("</code>"))
        self.assertNotRegex(clipped, r"&(?:#\w*|\w*)$|<[^>]*$")

    def test_main_menu_is_grouped_and_role_safe(self) -> None:
        user = {button["callback_data"] for row in TelegramPanelRuntimeV38.compact_menu_rows(False) for button in row}
        admin = {button["callback_data"] for row in TelegramPanelRuntimeV38.compact_menu_rows(True) for button in row}
        self.assertEqual(
            user,
            {"page:active", "page:analytics", "page:sources", "page:settings", "page:status"},
        )
        self.assertEqual(
            admin,
            {"page:active", "page:analytics", "page:sources", "page:settings", "page:control"},
        )
        for role_rows in (
            TelegramPanelRuntimeV38.compact_menu_rows(False),
            TelegramPanelRuntimeV38.compact_menu_rows(True),
        ):
            self.assertFalse(telegram_ui.markup_issues({"inline_keyboard": role_rows}))

    def test_active_wheels_are_paginated_for_a_small_phone(self) -> None:
        panel, captured = self.capture_panel(role="owner")
        items = [
            {
                "_key": f"wheel-{index:02d}",
                "identifier": f"wheel-{index:02d}",
                "source": "mechanogun",
                "url": f"https://betboom.ru/freestream/wheel-{index:02d}",
            }
            for index in range(ACTIVE_PAGE_SIZE * 2 + 2)
        ]
        snap = self.snapshot()
        panel._collect_current_wheels = lambda: items  # type: ignore[method-assign]
        panel.snapshot = lambda force=False: snap  # type: ignore[method-assign]
        panel._joined_wheel_keys = lambda value: set()  # type: ignore[method-assign]
        panel._sources_for_item = lambda value, key, item: ["mechanogun"]  # type: ignore[method-assign]
        panel._monitor_status = lambda: {}  # type: ignore[method-assign]
        panel.show_active(1)
        text, kwargs = captured[-1]
        self.assertIn("Страница: <b>2 из 3</b>", text)
        self.assertIn("wheel-06", text)
        self.assertIn("wheel-11", text)
        self.assertNotIn("wheel-12", text)
        markup = kwargs["reply_markup"]
        self.assertFalse(telegram_ui.markup_issues(markup))
        self.assertIn("page:active:0", str(markup))
        self.assertIn("page:active:2", str(markup))

    def test_long_wheel_key_uses_a_safe_resolvable_callback(self) -> None:
        panel, captured = self.capture_panel(role="owner")
        key = "ключ:" + "очень-длинный-" * 20
        item = {
            "_key": key,
            "identifier": key,
            "source": "mechanogun",
            "url": "https://betboom.ru/freestream/long",
        }
        snap = self.snapshot()
        panel._collect_current_wheels = lambda: [item]  # type: ignore[method-assign]
        panel.snapshot = lambda force=False: snap  # type: ignore[method-assign]
        panel._joined_wheel_keys = lambda value: set()  # type: ignore[method-assign]
        panel._sources_for_item = lambda value, current, row: ["mechanogun"]  # type: ignore[method-assign]
        panel._monitor_status = lambda: {}  # type: ignore[method-assign]
        panel.show_active()
        markup = captured[-1][1]["reply_markup"]
        callbacks = [
            str(button.get("callback_data"))
            for button in flat_buttons(markup)
            if button.get("callback_data", "").startswith("wheel:")
        ]
        self.assertTrue(callbacks)
        self.assertTrue(any(":~" in callback for callback in callbacks))
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callbacks))
        token = next(value.rsplit(":", 1)[1] for value in callbacks if ":~" in value)
        self.assertEqual(panel._resolve_wheel_token(token), key.casefold())

    def test_hashed_wheel_callback_reaches_the_original_admin_action(self) -> None:
        panel, _captured = self.capture_panel(role="owner")
        key = "ключ:" + "длинный-" * 30
        item = {"_key": key, "identifier": key, "source": "mechanogun"}
        calls: list[tuple[str, str]] = []
        panel._collect_current_wheels = lambda: [item]  # type: ignore[method-assign]
        panel._prepare_callback_user = lambda query: None  # type: ignore[method-assign]
        panel.set_context = lambda chat_id, user_id: None  # type: ignore[method-assign]
        panel.answer = lambda query_id, text="": None  # type: ignore[method-assign]
        panel.refresh_snapshot = lambda: None  # type: ignore[method-assign]
        panel.dispatch_admin_action = lambda action, value: calls.append((action, value)) or {  # type: ignore[method-assign]
            "queued": True,
            "detail": "queued",
        }
        callback = panel._wheel_callback("part", key)
        panel.handle_callback(
            {
                "id": "long-key",
                "data": callback,
                "from": {"id": 1},
                "message": {"message_id": 1, "chat": {"id": 1, "type": "private"}},
            }
        )
        self.assertIn(("participate_wheel", key.casefold()), calls)

    def test_quick_time_template_resolves_a_hashed_wheel_key(self) -> None:
        panel, _captured = self.capture_panel(role="owner")
        key = "https://example.invalid/" + "wheel/" * 30
        item = {"_key": key, "identifier": key, "source": "mechanogun"}
        calls: list[tuple[str, str]] = []
        panel._collect_current_wheels = lambda: [item]  # type: ignore[method-assign]
        panel.dispatch_admin_action = lambda action, value: calls.append((action, value)) or {  # type: ignore[method-assign]
            "queued": True
        }
        panel.pending_input = {1: {"kind": "wheel_time", "key": key}}
        token = panel._quick_time_callback(key, 75).split(":", 2)[2].rsplit(":", 1)[0]
        panel._set_quick_time(token, 75)
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "set_deadline")
        self.assertTrue(calls[0][1].startswith(key.casefold() + "|"))

    def test_analytics_uses_clear_names_and_matches_visible_active_list(self) -> None:
        panel, captured = self.capture_panel(role="user")
        today = datetime.now(legacy_timezone()).date().isoformat()
        snap = self.snapshot(
            state={
                "active_wheels": {
                    "visible": {"identifier": "visible"},
                    "expired": {"identifier": "expired"},
                },
                "participating_wheels": {},
            },
            stats={
                "daily": {
                    today: {
                        "totals": {"wheel_posts": 3, "preliminary_sent": 99},
                        "sources": {
                            "mechanogun": {"wheel_posts": 2},
                            "collector": {"wheel_posts": 1},
                        },
                    }
                },
                "sources": {},
            },
        )
        panel.snapshot = lambda force=False: snap  # type: ignore[method-assign]
        panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
            {"_key": "visible", "identifier": "visible"}
        ]
        panel._joined_wheel_keys = lambda value: {"visible"}  # type: ignore[method-assign]
        panel.show_analytics(7)
        text, kwargs = captured[-1]
        self.assertIn("Постов с колёсами", text)
        self.assertIn("Каналов с находками", text)
        self.assertIn("Активных колёс: <b>1</b>", text)
        self.assertIn("С вашей отметкой: <b>1 из 1</b>", text)
        self.assertNotIn("Публикац", text)
        self.assertNotIn("Уведомлений:", text)
        self.assertIn("✓ 7 дней", str(kwargs["reply_markup"]))

    def test_rating_hides_internal_confirmation_counter(self) -> None:
        panel, captured = self.capture_panel(role="user")
        panel.snapshot = lambda force=False: self.snapshot(  # type: ignore[method-assign]
            stats={
                "daily": {},
                "sources": {
                    "mechanogun": {
                        "quality_score": 80,
                        "admin_confirmed_wheels": 2,
                    }
                },
            }
        )
        panel.show_ranking()
        text = captured[-1][0]
        self.assertIn("@mechanogun", text)
        self.assertIn("80</b> оч.", text)
        self.assertNotIn("подтвержд.)", text)
        self.assertNotIn("2 подтвержд", text)

    def test_system_status_has_no_admin_button_for_user(self) -> None:
        for role in ("user", "admin"):
            panel, captured = self.capture_panel(role=role)
            snap = self.snapshot(fast=["one"], nightly=[])
            panel.snapshot = lambda force=False, value=snap: value  # type: ignore[method-assign]
            panel._monitor_status = lambda: {  # type: ignore[method-assign]
                "last_successful_iteration_at": datetime.now(UTC).isoformat(),
                "checked_sources": 1,
                "reachable_sources": 1,
                "source_errors": 0,
            }
            panel.load_source_registry = lambda: {  # type: ignore[method-assign]
                "summary": {"total": 1}
            }
            panel._collect_current_wheels = lambda: []  # type: ignore[method-assign]
            panel.show_status()
            markup = str(captured[-1][1]["reply_markup"])
            if role == "user":
                self.assertNotIn("control:monitor", markup)
            else:
                self.assertIn("control:monitor", markup)

    def test_owner_user_list_is_paginated(self) -> None:
        panel, captured = self.capture_panel(role="owner")
        users = {
            str(index): {
                "id": str(index),
                "chat_id": str(index),
                "first_name": f"Пользователь {index:02d}",
            }
            for index in range(ACCESS_PAGE_SIZE * 2 + 3)
        }
        access = {"owner_id": "0", "admins": ["1"], "users": users}
        panel.load_access = lambda force=False: access  # type: ignore[method-assign]
        panel.role_for = lambda user_id: "owner" if str(user_id) == "0" else "admin" if str(user_id) == "1" else "user"  # type: ignore[method-assign]
        panel.show_access(1)
        text, kwargs = captured[-1]
        self.assertIn("Страница: <b>2 из 3</b>", text)
        self.assertIn("Пользователь 08", text)
        self.assertIn("Пользователь 15", text)
        self.assertNotIn("Пользователь 16", text)
        detail_buttons = [
            button
            for button in flat_buttons(kwargs["reply_markup"])
            if str(button.get("callback_data", "")).startswith("page:user:")
        ]
        self.assertEqual(len(detail_buttons), ACCESS_PAGE_SIZE)
        self.assertFalse(telegram_ui.markup_issues(kwargs["reply_markup"]))

    def test_inactive_source_report_is_paginated(self) -> None:
        panel, captured = self.capture_panel(role="admin")
        sources = [f"source_{index:02d}" for index in range(23)]
        snap = self.snapshot(stats={"sources": {}, "daily": {}})
        panel.snapshot = lambda force=False: snap  # type: ignore[method-assign]
        panel.source_sets = lambda value: {"inactive": sources}  # type: ignore[method-assign]
        panel.show_inactive_report(1)
        text, kwargs = captured[-1]
        self.assertIn("Страница: <b>2 из 3</b>", text)
        self.assertIn("@source_10", text)
        self.assertIn("@source_19", text)
        self.assertNotIn("@source_20", text)
        self.assertFalse(telegram_ui.markup_issues(kwargs["reply_markup"]))

    def test_period_switch_replaces_stack_instead_of_building_a_dead_end(self) -> None:
        panel, _captured = self.capture_panel(role="user")
        panel.navigation = {"1": ["menu", "analytics:1"]}
        rendered: list[str] = []
        panel.render_page = lambda page: rendered.append(page)  # type: ignore[method-assign]
        panel.open_page("stats:7")
        panel.open_page("report:30")
        self.assertEqual(panel.navigation["1"], ["menu", "analytics:30"])
        self.assertEqual(rendered, ["analytics:7", "analytics:30"])

    def test_obsolete_app_button_stays_safe_and_does_not_reopen_miniapp(self) -> None:
        panel, captured = self.capture_panel(role="user")
        panel.render_page("app")
        text, kwargs = captured[-1]
        self.assertIn("временно отключено", text)
        self.assertNotIn("web_app", str(kwargs.get("reply_markup")))

    def test_primary_screens_fit_telegram_for_every_role(self) -> None:
        for role in ("user", "admin", "owner"):
            panel, captured = self.capture_panel(role=role)
            access = {
                "owner_id": "1" if role == "owner" else "9",
                "admins": ["1"] if role == "admin" else [],
                "users": {
                    "1": {
                        "id": "1",
                        "chat_id": "1",
                        "first_name": "Тест",
                        "notification_preferences": {"wheels": True},
                    }
                },
                "settings": {"monitor_interval_minutes": 5},
                "notification_recipients": ["1"],
            }
            snap = self.snapshot()
            panel.snapshot = lambda force=False, value=snap: value  # type: ignore[method-assign]
            panel.load_access = lambda force=False, value=access: value  # type: ignore[method-assign]
            panel.role_for = lambda user_id, value=role: value  # type: ignore[method-assign]
            panel.load_source_registry = lambda: {  # type: ignore[method-assign]
                "generated_at": datetime.now(UTC).isoformat(),
                "summary": {
                    "total": 1,
                    "checked": 1,
                    "available": 1,
                    "unavailable": 0,
                    "pending": 0,
                },
                "sources": [],
            }
            panel._monitor_status = lambda: {  # type: ignore[method-assign]
                "last_successful_iteration_at": datetime.now(UTC).isoformat(),
                "checked_sources": 1,
                "reachable_sources": 1,
                "source_errors": 0,
            }
            panel._collect_current_wheels = lambda: []  # type: ignore[method-assign]
            panel._joined_wheel_keys = lambda value: set()  # type: ignore[method-assign]
            panel.notification_preferences = lambda user_id=None: {  # type: ignore[method-assign]
                "wheels": True,
                "wheel_final_reminders": True,
                "wheel_draw_alerts": False,
                "admin_system": role in {"admin", "owner"},
                "admin_sources": role in {"admin", "owner"},
                "admin_requests": role in {"admin", "owner"},
            }
            screens = [
                ("menu", lambda: panel.show_menu()),
                ("active:0", lambda: panel.show_active()),
                ("analytics:1", lambda: panel.show_analytics()),
                ("sources", lambda: panel.show_sources()),
                ("settings", lambda: panel.show_settings()),
                ("notifications", lambda: panel.show_notifications()),
                ("status", lambda: panel.show_status()),
                ("ranking", lambda: panel.show_ranking()),
            ]
            if role in {"admin", "owner"}:
                screens.append(("control", lambda: panel.show_control()))
            if role == "owner":
                screens.append(("access:0", lambda: panel.show_access()))
            for page, render in screens:
                panel.navigation["1"] = ["menu"] if page == "menu" else ["menu", page]
                render()
                text, kwargs = captured[-1]
                self.assertLessEqual(len(text), telegram_ui.TELEGRAM_TEXT_LIMIT, (role, page))
                markup = kwargs.get("reply_markup")
                self.assertFalse(telegram_ui.markup_issues(markup), (role, page, markup))


def legacy_timezone():
    # The production period boundary is Asia/Barnaul; importing through the
    # helper keeps the test aligned with the actual panel configuration.
    import admin_bot

    return admin_bot.DISPLAY_TZ


if __name__ == "__main__":
    unittest.main()
