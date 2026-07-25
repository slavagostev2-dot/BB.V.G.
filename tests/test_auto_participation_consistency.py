from __future__ import annotations

from datetime import datetime, timezone

import auto_participation_notifications
import betboom_auto_participation as auto
import monitor

UTC = timezone.utc


def event() -> dict:
    return {
        "wheel_key": "zonertg16",
        "identifier": "zonertg16",
        "action_id": 701,
        "server_start_at": "2026-07-25T08:36:46.419000+00:00",
        "event_id": "6b6a163030b5ef75219f",
        "message_date": "2026-07-25T08:36:49+00:00",
        "url": "https://betboom.ru/freestream/zonertg16",
    }


def test_action_identity_wins_over_internal_event_id() -> None:
    assert auto._event_token("zonertg16", event()) == (
        "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00"
    )


def test_legacy_success_cannot_be_replaced_by_later_failure() -> None:
    state = {
        "active_wheels": {"zonertg16": event()},
        "auto_participation_events": {
            "zonertg16#event:6b6a163030b5ef75219f": {
                "wheel_key": "zonertg16",
                "status": "participated",
                "detail": "post_click_layout:main:Об акции",
                "attempted_at": "2026-07-25T09:05:17+00:00",
                "bot_success_pending_at": "2026-07-25T09:05:35+00:00",
            },
            "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00": {
                "wheel_key": "zonertg16",
                "status": "button_not_found",
                "attempted_at": "2026-07-25T09:06:21+00:00",
                "bot_failure_pending_at": "2026-07-25T09:05:36+00:00",
            },
        },
    }
    assert auto.canonicalize_primary_event_aliases(state)
    token = "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00"
    record = state["auto_participation_events"][token]
    assert record["status"] == "participated"
    assert record["account_key"] == "vyacheslav_primary"
    assert "bot_failure_pending_at" not in record
    assert "zonertg16#event:6b6a163030b5ef75219f" not in state["auto_participation_events"]


def test_owner_registry_waits_for_all_enabled_owner_accounts() -> None:
    base = "wheel#action:42:start"
    state = {
        "auto_participation_account_registry": {
            "vyacheslav_primary": {
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "account_owner": "vyacheslav",
                "account_order": 10,
                "enabled": True,
            },
            "vyacheslav_secondary": {
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "account_owner": "vyacheslav",
                "account_order": 20,
                "enabled": True,
            },
            "vyacheslav_spare": {
                "account_key": "vyacheslav_spare",
                "account_label": "Резервный аккаунт",
                "account_owner": "vyacheslav",
                "account_order": 30,
                "enabled": True,
            },
            "xflarxx_primary": {
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "account_owner": "xflarxx",
                "account_order": 10,
                "enabled": True,
            },
        },
        "active_wheels": {
            "wheel": {"wheel_key": "wheel", "action_id": 42, "server_start_at": "start"}
        },
        "auto_participation_events": {
            base: {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "status": "participated",
                "bot_success_pending_at": "2026-07-25T09:00:00+00:00",
            },
            base + "#account:vyacheslav_secondary": {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "status": "participated",
                "bot_success_pending_at": "2026-07-25T09:00:01+00:00",
            },
            base + "#account:xflarxx_primary": {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "status": "participated",
                "bot_success_pending_at": "2026-07-25T09:00:02+00:00",
            },
        },
    }
    assert not auto_participation_notifications._settled_event_groups(
        state, now=datetime(2026, 7, 25, 9, 10, tzinfo=UTC)
    )
    state["auto_participation_events"][base + "#account:vyacheslav_spare"] = {
        "wheel_key": "wheel",
        "event_token": base,
        "account_key": "vyacheslav_spare",
        "account_label": "Резервный аккаунт",
        "account_owner": "vyacheslav",
        "account_order": 30,
        "status": "participated",
        "bot_success_pending_at": "2026-07-25T09:00:03+00:00",
    }
    groups = auto_participation_notifications._settled_event_groups(
        state, now=datetime(2026, 7, 25, 9, 10, tzinfo=UTC)
    )
    accounts = groups[base]
    assert set(accounts) == {
        "vyacheslav_primary",
        "vyacheslav_secondary",
        "vyacheslav_spare",
    }
    text, _ = auto_participation_notifications._result_message(
        "wheel", {"identifier": "wheel"}, accounts
    )
    assert "Аккаунт 1" in text
    assert "Аккаунт 2" in text
    assert "Резервный аккаунт" in text
    assert "xFLARXx" not in text


def test_notification_persists_exact_event_before_dispatch(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(monitor, "send_message", lambda *args, **kwargs: calls.append("send") or {"ok": True})

    def save(state: dict) -> None:
        item = state["active_wheels"]["zonertg16"]
        assert item["action_id"] == 701
        assert item["server_start_at"] == "2026-07-25T08:36:46.419000+00:00"
        calls.append("save")

    monkeypatch.setattr(monitor, "save_state", save)
    monkeypatch.setattr(
        monitor,
        "process_auto_participation_dispatch",
        lambda state: calls.append("dispatch") or True,
    )
    message = monitor.Message(
        source="mechanogun",
        message_id=35756,
        date=datetime(2026, 7, 25, 8, 36, 49, tzinfo=UTC),
        text="wheel",
        message_url="https://telegram.me/mechanogun/35756",
    )
    state: dict = {"active_wheels": {}, "button_contexts": {}, "participating_wheels": {}}
    monitor.notify_new_link(
        message,
        "https://betboom.ru/freestream/zonertg16",
        datetime(2026, 7, 25, 18, 36, 46, tzinfo=UTC),
        "активность подтверждена",
        [],
        state,
        action_id=701,
        server_start_at=datetime(2026, 7, 25, 8, 36, 46, 419000, tzinfo=UTC),
    )
    assert calls == ["send", "save", "dispatch"]
