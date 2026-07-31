from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import auto_participation_notifications
import auto_participation_owner_sync
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
        "evt:6b6a163030b5ef75219f"
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
    token = "evt:6b6a163030b5ef75219f"
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
    canonical = auto_participation_owner_sync._event_token(
        state["active_wheels"]["wheel"],
        "wheel",
    )
    accounts = groups[canonical]
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


def test_referral_success_on_one_account_sends_one_honest_owner_result() -> None:
    base = "evt:1393d78ddd6274d3103d"
    item = {
        "wheel_key": "kekw2",
        "identifier": "kekw2",
        "source": "shadowkekw",
        "event_id": "1393d78ddd6274d3103d",
        "message_text": "Колесико для рефов",
        "referral_restricted": True,
    }
    state = {
        "active_wheels": {"kekw2": item},
        "auto_participation_events": {
            base: {
                "wheel_key": "kekw2",
                "event_token": base,
                "event_context": item,
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "status": "unconfirmed",
                "bot_failure_status": "unconfirmed",
                "bot_failure_pending_at": "2026-07-30T15:00:00+00:00",
            },
            base + "#account:vyacheslav_secondary": {
                "wheel_key": "kekw2",
                "event_token": base,
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "status": "participated",
                "bot_success_pending_at": "2026-07-30T15:00:10+00:00",
            },
        },
    }
    groups = auto_participation_notifications._settled_event_groups(
        state,
        now=datetime(2026, 7, 30, 15, 5, tzinfo=UTC),
    )
    assert len(groups) == 1
    accounts = next(iter(groups.values()))
    assert auto_participation_notifications._should_send_event_result(
        {"notification_preferences": {}},
        item,
        accounts,
    )
    text, _ = auto_participation_notifications._result_message(
        "kekw2",
        item,
        accounts,
    )
    assert "Реферальное колесо — участие доступно" in text
    assert "Источник: @shadowkekw" in text
    assert "⏳ Аккаунт 1 — участие пока не подтверждено" in text
    assert "✅ Аккаунт 2 — участие подтверждено BetBoom" in text
    assert "❌ Аккаунт 1" not in text
    assert "no exact post-click confirmation" not in text

    assert auto_participation_notifications._should_finalize(
        {},
        {
            "completed_at": "2026-07-30T15:06:00+00:00",
            "notification_sent": False,
            "notification_policy": "referral_suppressed",
        },
        all_success=False,
        allow_referral_upgrade=True,
    )
    assert not auto_participation_notifications._should_send_event_result(
        {"notification_preferences": {}},
        item,
        {
            key: (token, record, False)
            for key, (token, record, _success) in accounts.items()
        },
    )


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
    assert calls == ["save", "dispatch", "send"]


def _browser_monitor() -> SimpleNamespace:
    def mark_participating(state: dict, context: dict) -> None:
        state.setdefault("participating_wheels", {})[context["wheel_key"]] = dict(
            context
        )

    return SimpleNamespace(
        now_utc=lambda: datetime(2026, 7, 25, 8, 40, tzinfo=UTC),
        parse_datetime=lambda _value: None,
        WHEEL_VERIFICATION_FAILED="failed",
        is_participating=lambda *_args: (_ for _ in ()).throw(
            AssertionError("Telegram mark must not suppress BetBoom verification")
        ),
        mark_participating=mark_participating,
    )


def test_telegram_participation_mark_does_not_skip_betboom_check(
    monkeypatch,
) -> None:
    item = event()
    state = {
        "auto_participation_event_mode_initialized_at": "2026-07-25T08:35:00+00:00",
        "active_wheels": {"zonertg16": item},
        "participating_wheels": {
            "zonertg16": {
                "participation_source": "telegram_personal_vote",
                "marked_at": "2026-07-25T08:39:00+00:00",
            }
        },
        "auto_participation_events": {},
    }
    calls: list[str] = []
    monkeypatch.setattr(auto, "configured", lambda: True)
    monkeypatch.setattr(
        auto,
        "participate",
        lambda url: calls.append(url)
        or auto.ParticipationResult(
            True,
            "already_participating",
            "BetBoom already confirms participation",
        ),
    )

    result = auto.process_new_wheel_events(state, _browser_monitor())

    token = auto._event_token("zonertg16", item)
    record = state["auto_participation_events"][token]
    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    assert calls == [item["url"]]
    assert record["status"] == "already_participating"
    assert record["confirmation_method"] == "betboom_preexisting"
    assert record["participation_origin"] == "preexisting_verified"
    assert item["auto_participation_origin"] == "preexisting_verified"


def test_legacy_bot_only_success_is_rearmed_and_verified(monkeypatch) -> None:
    item = event()
    token = auto._event_token("zonertg16", item)
    state = {
        "auto_participation_event_mode_initialized_at": "2026-07-25T08:35:00+00:00",
        "active_wheels": {"zonertg16": item},
        "auto_participation_events": {
            token: {
                "wheel_key": "zonertg16",
                "event_token": token,
                "status": "already_marked_in_bot",
                "recorded_at": "2026-07-25T08:39:00+00:00",
            }
        },
    }
    monkeypatch.setattr(auto, "configured", lambda: True)
    monkeypatch.setattr(
        auto,
        "participate",
        lambda _url: auto.ParticipationResult(
            True,
            "already_participating",
            "BetBoom already confirms participation",
        ),
    )

    result = auto.process_new_wheel_events(state, _browser_monitor())

    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    assert state["auto_participation_events"][token]["status"] == (
        "already_participating"
    )
    assert item["auto_participation_rearm_reason"] == (
        "bot_mark_requires_betboom_verification"
    )

