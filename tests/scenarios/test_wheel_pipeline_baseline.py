from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import admin_action_queue
import auto_participation_notifications
import auto_participation_owner_sync
import betboom_auto_participation
import bbvg_monitor_main


UTC = timezone.utc
monitor = bbvg_monitor_main.monitor


def _empty_state() -> dict[str, Any]:
    return {
        "version": 6,
        "initialized_sources": [],
        "seen": {},
        "url_alerts": {},
        "activation_alerts": {},
        "pending_posts": {},
        "health": {},
        "button_contexts": {},
        "manual_overrides": {},
        "telegram_update_offset": 0,
        "active_wheels": {},
        "participating_wheels": {},
        "wheel_action_history": {},
        "wheel_generation_observations": {},
        "wheel_publications": {},
        "recently_completed_wheels": {},
        "inactive_wheels": {},
        "completed_wheel_alerts": {},
        "manual_deadlines": {},
        "auto_participation_events": {},
        "auto_participation_dispatch_events": {},
        "bot_commands_version": 0,
    }


class _FakePanel:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.access = {
            "owner_id": "owner",
            "admins": [],
            "users": {
                "owner": {
                    "chat_id": "1001",
                    "notification_preferences": {"auto_participation": True},
                    "auto_participation_success_events": {},
                    "auto_participation_failure_events": {},
                }
            },
        }
        self.access_loaded = True
        self.current_chat_id: str | None = None
        self.current_user_id: str | None = None
        self.current_role = "guest"
        self.sent: list[dict[str, Any]] = []
        self.saved: list[str] = []

    def snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(state=self.state)

    def load_access(self, force: bool = False) -> dict[str, Any]:
        del force
        return self.access

    def save_access(self, message: str) -> None:
        self.saved.append(message)

    def set_context(self, chat_id: str, user_id: str) -> None:
        self.current_chat_id = chat_id
        self.current_user_id = user_id
        self.current_role = "owner"

    def mark_personal_participation(self, wheel_key: str) -> dict[str, Any]:
        return {
            "changed": True,
            "weight": 5,
            "vote_command_id": f"baseline:{wheel_key}",
        }

    def _sources_for_item(
        self,
        snapshot: SimpleNamespace,
        wheel_key: str,
        item: dict[str, Any],
    ) -> list[str]:
        del snapshot, wheel_key, item
        return ["source_one", "source_two"]

    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.sent.append(
            {
                "text": text,
                "reply_markup": reply_markup,
                "chat_id": chat_id,
            }
        )


def test_current_production_composition_is_frozen() -> None:
    """Stage 1 records the current composition without changing its behavior."""

    assert monitor.BOT_FEEDBACK_ENABLED is False
    assert monitor.process_admin_actions is admin_action_queue.process_pending
    assert monitor._bbvg_wheel_event_runtime_installed is True
    assert monitor._bbvg_restart_duplicate_guard_installed is True
    assert monitor._bbvg_wheel_link_lifecycle_installed is True
    assert monitor._bbvg_wheel_lifecycle_v2_installed is True
    assert monitor._bbvg_personal_reminder_filter_installed is True
    assert callable(monitor.process_auto_participation_dispatch)


def test_active_publication_reaches_one_combined_auto_participation_result(
    monkeypatch: Any,
) -> None:
    """Freeze the current discovery -> notification -> participation -> result contract."""

    now = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
    deadline = now + timedelta(minutes=30)
    server_start = now - timedelta(minutes=5)
    url = "https://betboom.ru/freestream/baseline-wheel"
    key = "baseline-wheel"
    message = monitor.Message(
        source="source_one",
        message_id=501,
        date=now - timedelta(minutes=1),
        text=f"Новое колесо {url}",
        message_url="https://telegram.me/source_one/501",
    )
    state = _empty_state()

    monkeypatch.setattr(monitor, "now_utc", lambda: now)
    monkeypatch.setattr(
        monitor,
        "inspect_wheel_page",
        lambda _url: monitor.WheelInspection(
            status="active",
            deadline=deadline,
            method="baseline BetBoom API",
            action_id=1201,
            verification_status=monitor.WHEEL_VERIFICATION_CONFIRMED,
            server_start_at=server_start,
        ),
    )

    assessment = monitor.assess_new_wheel(message, url, state)
    assert assessment.should_notify is True
    assert assessment.status == "active"
    assert assessment.action_id == 1201
    assert assessment.server_start_at == server_start

    initial_messages: list[dict[str, Any]] = []

    def capture_initial(
        text: str,
        url: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        initial_messages.append(
            {"text": text, "url": url, "reply_markup": reply_markup}
        )
        return {"ok": True, "result": {"sent": 1, "message_id": 9001}}

    monkeypatch.setattr(monitor, "send_message", capture_initial)
    monitor.notify_new_link(
        message,
        url,
        assessment.deadline,
        assessment.method,
        [],
        state,
        assessment.page_excerpt,
        action_id=assessment.action_id,
        available_at=assessment.available_at,
        verification_status=assessment.verification_status,
        server_start_at=assessment.server_start_at,
    )

    assert len(initial_messages) == 1
    assert "Новое колесо BetBoom" in initial_messages[0]["text"]
    entry = state["active_wheels"][key]
    assert entry["action_id"] == 1201
    assert entry["server_start_at"] == server_start.isoformat()
    assert betboom_auto_participation._eligible_for_event_attempt(
        entry, monitor, now
    )

    result = betboom_auto_participation.ParticipationResult(
        True,
        "participated",
        "BetBoom подтвердил участие",
    )
    betboom_auto_participation._mark_confirmed_participation(
        state,
        monitor,
        key,
        entry,
        result,
        now,
    )
    assert state["active_wheels"][key]["participating"] is True

    # The live Control Center groups the two owner accounts by this exact event.
    base_token = auto_participation_owner_sync._event_token(entry, key)
    event_context = {
        field: entry[field]
        for field in (
            "identifier",
            "wheel_key",
            "url",
            "source",
            "message_id",
            "message_date",
            "message_url",
            "action_id",
            "server_start_at",
            "deadline",
        )
        if field in entry
    }
    state["auto_participation_events"] = {
        base_token: {
            "wheel_key": key,
            "account_key": auto_participation_notifications.PRIMARY_ACCOUNT_KEY,
            "account_label": auto_participation_notifications.PRIMARY_ACCOUNT_LABEL,
            "status": "participated",
            "attempted_at": now.isoformat(),
            "bot_success_pending_at": now.isoformat(),
            "event_context": event_context,
        },
        f"{base_token}#account:{auto_participation_notifications.SECONDARY_ACCOUNT_KEY}": {
            "wheel_key": key,
            "event_token": base_token,
            "account_key": auto_participation_notifications.SECONDARY_ACCOUNT_KEY,
            "account_label": auto_participation_notifications.SECONDARY_ACCOUNT_LABEL,
            "status": "participated",
            "attempted_at": now.isoformat(),
            "bot_success_pending_at": now.isoformat(),
            "event_context": event_context,
        },
    }

    # Avoid coupling this scenario to the separate HMAC message-id lookup.
    entry.pop("button_token", None)
    panel = _FakePanel(state)
    summary = auto_participation_notifications.sync_once(panel)

    assert summary["completed"] == 1
    assert summary["success_completed"] == 1
    assert len(panel.sent) == 1
    assert panel.sent[0]["chat_id"] == "1001"
    assert "Участие принято" in panel.sent[0]["text"]
    assert "Аккаунты: <b>1 и 2</b>" in panel.sent[0]["text"]
    assert panel.saved == [
        "Record automatic participation success for owner [skip ci]"
    ]
