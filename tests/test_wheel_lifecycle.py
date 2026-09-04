"""Wheel lifecycle and rating contracts."""

from __future__ import annotations

import unittest
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_v3
import monitor
import notification_router
import wheel_event_runtime
import wheel_lifecycle_v2
import wheel_publications_v2
from bbvg.bot.wheels import WheelInteractionRuntime


UTC = timezone.utc


def event_state(*, message_id: int, message_date: datetime) -> dict[str, Any]:
    entry = {
        "identifier": "wheel-a",
        "url": "https://betboom.ru/freestream/wheel-a",
        "source": "official",
        "message_id": message_id,
        "message_date": message_date.isoformat(),
        "message_url": f"https://telegram.me/official/{message_id}",
        "message_text": "Колесо https://betboom.ru/freestream/wheel-a",
        "deadline": (message_date + timedelta(hours=3)).isoformat(),
    }
    return {
        "active_wheels": {"wheel-a": entry},
        "participating_wheels": {},
        "pending_posts": {},
        "button_contexts": {},
        "completed_wheel_alerts": {},
        "manual_deadlines": {},
        "manual_overrides": {},
        "wheel_publications": {
            "wheel-a": [
                {
                    "source": "official",
                    "message_id": message_id,
                    "message_date": message_date.isoformat(),
                },
                {
                    "source": "collector",
                    "message_id": message_id + 1000,
                    "message_date": (message_date + timedelta(minutes=1)).isoformat(),
                },
            ]
        },
        "inactive_wheels": {},
        "recently_completed_wheels": {},
    }


class Chapter5LifecycleTests(unittest.TestCase):
    def test_manual_submission_uses_standard_notification_and_deduplication(self) -> None:
        now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
        deadline = now + timedelta(minutes=20)
        payload = json.dumps({
            "submitted_at": now.isoformat(),
            "url": "https://betboom.ru/freestream/ManualWheel",
        })
        state: dict[str, Any] = {
            "active_wheels": {},
            "participating_wheels": {},
            "pending_posts": {},
            "seen": {},
            "button_contexts": {},
            "manual_overrides": {},
            "url_alerts": {},
            "activation_alerts": {},
            "wheel_publications": {},
        }
        notifications: list[str] = []
        assessment = monitor.WheelAssessment(
            True,
            deadline,
            "активность и таймер подтверждены BetBoom",
            "active",
            action_id=701,
            verification_status=monitor.WHEEL_VERIFICATION_CONFIRMED,
            server_start_at=now,
        )

        def notify(message, url, deadline_value, method, mappings, state_value, page_excerpt="", **metadata):
            notifications.append(url)
            monitor.remember_active_wheel(
                state_value,
                message,
                url,
                deadline_value,
                "active",
                method,
                page_excerpt,
                **metadata,
            )

        original_assess = monitor.assess_new_wheel
        original_notify = monitor.notify_new_link
        original_mappings = monitor.load_identifier_sources
        original_activation_suppressed = monitor.is_activation_suppressed
        monitor.assess_new_wheel = lambda message, url, state_value=None: assessment
        monitor.notify_new_link = notify
        monitor.load_identifier_sources = lambda: []
        monitor.is_activation_suppressed = lambda state_value, url: bool(
            state_value.get("activation_alerts", {}).get(monitor.wheel_key(url))
        )
        try:
            first = admin_action_v3.apply_action_v3(
                state, {"sources": {}}, {}, "submit_wheel", payload
            )
            second = admin_action_v3.apply_action_v3(
                state, {"sources": {}}, {}, "submit_wheel", payload
            )
        finally:
            monitor.assess_new_wheel = original_assess
            monitor.notify_new_link = original_notify
            monitor.load_identifier_sources = original_mappings
            monitor.is_activation_suppressed = original_activation_suppressed

        self.assertTrue(first["notified"])
        self.assertFalse(second["notified"])
        self.assertEqual(notifications, ["https://betboom.ru/freestream/ManualWheel"])
        self.assertEqual(state["active_wheels"]["manualwheel"]["action_id"], 701)
        self.assertEqual(
            state["active_wheels"]["manualwheel"]["source"],
            monitor.MANUAL_SUBMISSION_SOURCE,
        )
        self.assertEqual(
            WheelInteractionRuntime._sources_for_item(
                SimpleNamespace(state=state),
                "manualwheel",
                state["active_wheels"]["manualwheel"],
            ),
            [],
        )

    def test_bot_accepts_one_manual_wheel_link_and_rejects_ambiguous_input(self) -> None:
        panel = object.__new__(WheelInteractionRuntime)
        panel.current_user_id = "42"
        panel.current_chat_id = "42"
        panel.current_role = "admin"
        panel.pending_input = {42: {"kind": "wheel_submit"}}
        actions: list[tuple[str, str]] = []
        dispatches: list[tuple[str, Any]] = []
        messages: list[str] = []
        panel.set_context = lambda chat_id, user_id: setattr(panel, "current_user_id", str(user_id))
        panel.role_for = lambda user_id=None: "admin"
        panel.is_admin = lambda: True
        panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}
        panel.send = lambda text, **kwargs: messages.append(text) or {}
        panel.dispatch_admin_action = lambda action, value: actions.append((action, value)) or {}
        panel.dispatch = lambda workflow, inputs=None: dispatches.append((workflow, inputs)) or {}

        panel.handle_message({
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42},
            "text": "https://betboom.ru/freestream/HOOCH08?utm_source=test",
        })

        self.assertEqual(actions[0][0], "submit_wheel")
        submitted = json.loads(actions[0][1])
        self.assertEqual(submitted["url"], "https://betboom.ru/freestream/HOOCH08")
        self.assertNotIn("actor", submitted)
        self.assertEqual(dispatches[0][0], "monitor.yml")
        self.assertNotIn(42, panel.pending_input)

        panel.pending_input[42] = {"kind": "wheel_submit"}
        panel.handle_message({
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42},
            "text": (
                "https://betboom.ru/freestream/one "
                "https://betboom.ru/freestream/two"
            ),
        })
        self.assertEqual(len(actions), 1)
        self.assertIn(42, panel.pending_input)
        self.assertIn("одну прямую ссылку", messages[-1])

    def test_formal_transition_table_covers_every_required_terminal_path(self) -> None:
        transitions = set(wheel_lifecycle_v2.LIFECYCLE_TRANSITIONS)
        self.assertIn(
            ("detected", "future_availability", "scheduled_availability"),
            transitions,
        )
        self.assertIn(("detected", "known_draw_time", "scheduled_draw"), transitions)
        self.assertIn(
            ("detected", "unknown_draw_time", "active_unknown_time"),
            transitions,
        )
        self.assertIn(
            ("scheduled_availability", "availability_reached", "active_unknown_time"),
            transitions,
        )
        for source in {
            "scheduled_availability",
            "active_unknown_time",
            "scheduled_draw",
            "participating",
        }:
            self.assertIn((source, "admin_finished", "finished"), transitions)
            self.assertIn((source, "admin_inactive", "inactive"), transitions)

    def test_exact_relative_manual_and_unknown_time_have_clear_states(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        self.assertEqual(
            wheel_lifecycle_v2.lifecycle_state(
                {"available_at": (now + timedelta(hours=2)).isoformat()}, now
            ),
            "scheduled_availability",
        )
        for method in ("точное время", "относительное время", "время вручную"):
            entry = {
                "deadline": (now + timedelta(hours=3)).isoformat(),
                "method": method,
            }
            self.assertEqual(
                wheel_lifecycle_v2.lifecycle_state(entry, now), "scheduled_draw"
            )
        self.assertEqual(
            wheel_lifecycle_v2.lifecycle_state({}, now), "active_unknown_time"
        )

        available_at, _ = wheel_event_runtime.infer_availability(
            "Через 2 часа запущу колесо",
            now,
            lambda text, published: (published + timedelta(hours=2), "relative"),
        )
        self.assertEqual(available_at, now + timedelta(hours=2))

    def test_referral_restriction_is_visible_in_notification_and_active_list(self) -> None:
        restricted = (
            "Участие доступно только пользователям, зарегистрированным "
            "по промокоду автора. https://betboom.ru/freestream/ref-wheel"
        )
        regular = (
            "Новое колесо BetBoom и промокод на бонус. "
            "https://betboom.ru/freestream/regular-wheel"
        )
        self.assertTrue(wheel_publications_v2.is_referral_restricted(restricted))
        self.assertFalse(wheel_publications_v2.is_referral_restricted(regular))

        message = monitor.Message(
            source="refsource",
            message_id=77,
            date=datetime(2026, 7, 22, 8, 0, tzinfo=UTC),
            text=restricted,
            message_url="https://telegram.me/refsource/77",
        )
        state: dict[str, Any] = {
            "active_wheels": {},
            "participating_wheels": {},
            "wheel_action_history": {},
            "button_contexts": {},
        }
        monitor.remember_active_wheel(
            state,
            message,
            "https://betboom.ru/freestream/ref-wheel",
            None,
            "active",
            "test",
        )
        entry = state["active_wheels"]["ref-wheel"]
        self.assertTrue(entry["referral_restricted"])
        self.assertIn("только для рефералов", monitor.active_wheels_text(state).casefold())

        sent: list[str] = []
        original_send = monitor.send_message
        try:
            monitor.send_message = lambda text, **kwargs: sent.append(text) or {"ok": True}  # type: ignore[assignment]
            monitor.notify_new_link(
                message,
                "https://betboom.ru/freestream/ref-wheel",
                None,
                "test",
                [],
            )
        finally:
            monitor.send_message = original_send
        self.assertIn("Реферальное колесо", sent[0])
        self.assertIn("отдельно для каждого аккаунта", sent[0])

        panel_messages: list[str] = []
        panel = WheelInteractionRuntime.__new__(WheelInteractionRuntime)
        panel._collect_current_wheels = lambda: [dict(entry, _key="ref-wheel")]  # type: ignore[method-assign]
        panel.snapshot = lambda force=False: SimpleNamespace(state={"participating_wheels": {}})  # type: ignore[method-assign]
        panel._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
        panel.is_admin = lambda: False  # type: ignore[method-assign]
        panel.parse_dt = lambda value: None  # type: ignore[method-assign]
        panel.remaining = lambda value: ""  # type: ignore[method-assign]
        panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
        panel.send = lambda text, **kwargs: panel_messages.append(text) or {}  # type: ignore[method-assign]
        panel.show_active()
        self.assertIn("Колесо только для рефералов", panel_messages[0])

    def test_event_identity_stays_stable_when_a_second_source_is_merged(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        entry = {
            "source": "official",
            "message_id": 10,
            "message_date": now.isoformat(),
        }
        wheel_lifecycle_v2.stamp_lifecycle("wheel-a", entry, now)
        first = entry["event_id"]
        entry.update(
            {
                "source": "collector",
                "message_id": 20,
                "message_date": (now + timedelta(minutes=2)).isoformat(),
            }
        )
        wheel_lifecycle_v2.stamp_lifecycle(
            "wheel-a", entry, now + timedelta(minutes=2)
        )
        self.assertEqual(entry["event_id"], first)

    def test_reused_url_creates_a_new_rating_event_and_credits_all_sources(self) -> None:
        stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
        health: dict[str, Any] = {"sources": {}}
        first_at = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
        state = event_state(message_id=10, message_date=first_at)

        first = admin_action_v3.apply_action_v3(
            state, health, stats, "participate_wheel", "wheel-a"
        )
        self.assertTrue(first["stats_changed"])
        admin_action_v3.apply_action_v3(
            state, health, stats, "confirm_finished_global", "wheel-a|admin"
        )

        second_state = event_state(
            message_id=11, message_date=first_at + timedelta(days=1)
        )
        state["active_wheels"] = second_state["active_wheels"]
        state["wheel_publications"] = second_state["wheel_publications"]
        second = admin_action_v3.apply_action_v3(
            state, health, stats, "participate_wheel", "wheel-a"
        )

        self.assertTrue(second["stats_changed"])
        self.assertEqual(len(stats["admin_wheel_decisions"]), 2)
        self.assertTrue(
            all(key.startswith("wheel-a#") for key in stats["admin_wheel_decisions"])
        )
        for source in ("official", "collector"):
            self.assertEqual(stats["sources"][source]["quality_score"], 80)

    def test_finished_button_awards_points_once(self) -> None:
        state = event_state(
            message_id=20,
            message_date=datetime(2026, 7, 15, 10, 0, tzinfo=UTC),
        )
        stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
        result = admin_action_v3.apply_action_v3(
            state,
            {"sources": {}},
            stats,
            "confirm_finished_global",
            "wheel-a|admin",
        )
        self.assertTrue(result["state_changed"])
        self.assertTrue(result["stats_changed"])
        self.assertIn("рейтинг источников начислен", result["detail"])
        self.assertEqual(len(stats["admin_wheel_decisions"]), 1)
        for source in ("official", "collector"):
            self.assertEqual(stats["sources"][source]["quality_score"], 40)

        repeated = admin_action_v3.apply_action_v3(
            state,
            {"sources": {}},
            stats,
            "confirm_finished_global",
            "wheel-a|admin",
        )
        self.assertFalse(repeated["state_changed"])
        self.assertFalse(repeated["stats_changed"])
        self.assertEqual(len(stats["admin_wheel_decisions"]), 1)
        for source in ("official", "collector"):
            self.assertEqual(stats["sources"][source]["quality_score"], 40)

    def test_inactive_reverses_only_the_current_event_confirmation(self) -> None:
        stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
        health: dict[str, Any] = {"sources": {}}
        first_at = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
        state = event_state(message_id=30, message_date=first_at)
        admin_action_v3.apply_action_v3(
            state, health, stats, "participate_wheel", "wheel-a"
        )
        admin_action_v3.apply_action_v3(
            state, health, stats, "confirm_finished_global", "wheel-a|admin"
        )

        current = event_state(message_id=31, message_date=first_at + timedelta(days=1))
        state["active_wheels"] = current["active_wheels"]
        state["wheel_publications"] = current["wheel_publications"]
        admin_action_v3.apply_action_v3(
            state, health, stats, "participate_wheel", "wheel-a"
        )
        admin_action_v3.apply_action_v3(
            state, health, stats, "mark_inactive_global", "wheel-a|admin"
        )

        for source in ("official", "collector"):
            self.assertEqual(stats["sources"][source]["quality_score"], 40)
        self.assertNotIn("wheel-a", state["active_wheels"])
        self.assertEqual(
            state["inactive_wheels"]["wheel-a"]["lifecycle_state"], "inactive"
        )

    def test_terminal_transition_cleans_all_mutable_event_records(self) -> None:
        now = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        state = event_state(message_id=40, message_date=now - timedelta(hours=2))
        state["participating_wheels"]["wheel-a"] = {"identifier": "wheel-a"}
        state["pending_posts"]["post"] = {"wheel_key": "wheel-a"}
        state["button_contexts"]["token"] = {"wheel_key": "wheel-a"}
        state["completed_wheel_alerts"]["wheel-a"] = {"identifier": "wheel-a"}
        state["manual_deadlines"]["wheel-a"] = {"deadline": now.isoformat()}
        state["manual_overrides"]["wheel-a"] = {"deadline": now.isoformat()}
        entry = state["active_wheels"]["wheel-a"]

        removed = wheel_lifecycle_v2.complete_event(
            state,
            "wheel-a",
            entry,
            current=now,
            reason="deadline_reached",
            deadline=now,
        )

        self.assertGreaterEqual(removed, 7)
        for name in (
            "active_wheels",
            "participating_wheels",
            "pending_posts",
            "button_contexts",
            "completed_wheel_alerts",
            "manual_deadlines",
            "manual_overrides",
            "wheel_publications",
        ):
            self.assertFalse(state[name], name)
        self.assertEqual(
            state["recently_completed_wheels"]["wheel-a"]["completion_reason"],
            "deadline_reached",
        )

    def test_final_five_minute_reminder_replaces_generic_reminder(self) -> None:
        now = datetime(2026, 7, 15, 12, 56, tzinfo=UTC)
        sent: list[str] = []

        def original_process(state: dict, stats: dict) -> dict[str, Any]:
            entry = state["active_wheels"]["wheel-a"]
            if not entry.get("known_reminder_sent_at"):
                sent.append("generic")
            return {"changed": False, "removed": 0}

        fake = SimpleNamespace(
            process_active_wheels=original_process,
            now_utc=lambda: now,
            parse_datetime=wheel_lifecycle_v2.monitor_datetime,
            is_participating=lambda state, key: False,
            active_entry_message=lambda entry: SimpleNamespace(),
            send_message=lambda text, **kwargs: sent.append(text) or {"ok": True},
            human_remaining=lambda deadline: "4 мин.",
            wheel_reply_markup=lambda *args, **kwargs: {"inline_keyboard": []},
            data_store=SimpleNamespace(increment_stat=lambda *args, **kwargs: None),
        )
        wheel_lifecycle_v2.install(fake)
        state = event_state(
            message_id=50,
            message_date=now - timedelta(hours=1),
        )
        entry = state["active_wheels"]["wheel-a"]
        entry["deadline"] = (now + timedelta(minutes=4)).isoformat()

        fake.process_active_wheels(state, {"sources": {}, "daily": {}})

        self.assertEqual(len(sent), 1)
        self.assertIn("последний шанс", sent[0])
        self.assertTrue(entry.get("final_reminder_sent_at"))
        self.assertTrue(entry.get("known_reminder_sent_at"))

    def test_same_event_is_deduplicated_but_next_publication_is_not(self) -> None:
        markup_one = {
            "_bbvg_event_id": "evt:11111111111111111111",
            "inline_keyboard": [
                [{"text": "Участвую", "callback_data": "bb:p:event-one"}]
            ]
        }
        first = notification_router.notification_event_identity(
            "wheels",
            "Новое колесо BetBoom\nИдентификатор: <code>wheel-a</code>\n@one",
            None,
            markup_one,
        )
        repost = notification_router.notification_event_identity(
            "wheels",
            "Новое колесо BetBoom\nИдентификатор: <code>wheel-a</code>\n@two",
            None,
            {
                "_bbvg_event_id": "evt:11111111111111111111",
                "inline_keyboard": [
                    [{"text": "Участвую", "callback_data": "bb:p:event-other-post"}]
                ],
            },
        )
        next_event = notification_router.notification_event_identity(
            "wheels",
            "Новое колесо BetBoom\nИдентификатор: <code>wheel-a</code>",
            None,
            {
                "_bbvg_event_id": "evt:22222222222222222222",
                "inline_keyboard": [
                    [{"text": "Участвую", "callback_data": "bb:p:event-two"}]
                ]
            },
        )
        self.assertEqual(first, repost)
        self.assertNotEqual(first, next_event)


if __name__ == "__main__":
    unittest.main()
