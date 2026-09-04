from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import auto_participation_notifications
import auto_participation_bot_sync
import auto_participation_owner_sync
import auto_participation_recovery
import betboom_auto_participation as auto
import monitor
import wheel_publications_v2

UTC = timezone.utc


def test_runtime_merge_does_not_resurrect_delivered_recovery_card() -> None:
    record = {
        "first_notified_at": "2026-08-13T10:40:00.223391+00:00",
        "recovered_initial_notification_pending_at": "2026-08-13T10:41:00+00:00",
        "recovered_initial_notification_reason": "recovery_discovered_missing_event",
    }

    assert auto_participation_bot_sync._suppress_delivered_recovery_pending(record)
    assert "recovered_initial_notification_pending_at" not in record
    assert record["recovered_initial_notification_sent_at"] == (
        "2026-08-13T10:40:00.223391+00:00"
    )


def test_recovery_replaces_previous_generation_publication_metadata(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    scanned_at = datetime(2026, 8, 2, 10, 37, tzinfo=UTC)
    monkeypatch.setattr(monitor, "now_utc", lambda: scanned_at)
    current = {
        "wheel_key": "helin",
        "identifier": "helin",
        "url": "https://betboom.ru/freestream/helin",
        "source": "helin139burmalda",
        "message_id": 1077,
        "message_date": "2026-08-02T10:35:37+00:00",
        "message_url": "https://telegram.me/helin139burmalda/1077",
        "message_text": "current generation",
        "action_id": 1187,
        "server_start_at": "2026-08-02T10:36:16.686000+00:00",
        "deadline": "2026-08-02T10:56:16.686000+00:00",
    }
    state = {
        "active_wheels": {
            "helin": {
                **current,
                "source": "old-source",
                "message_id": 1050,
                "message_date": "2026-07-26T14:47:16+00:00",
                "message_url": "https://telegram.me/helin139burmalda/1050",
                "message_text": "previous generation",
                "action_id": 1039,
                "server_start_at": "2026-07-26T14:46:34.585000+00:00",
            }
        },
        "participating_wheels": {},
        "auto_participation_events": {},
    }

    auto_participation_recovery._restore_runtime_state(
        state,
        [current],
        [],
        scanned_at,
    )

    restored = state["active_wheels"]["helin"]
    assert restored["action_id"] == 1187
    assert restored["server_start_at"] == current["server_start_at"]
    assert restored["message_id"] == 1077
    assert restored["message_date"] == current["message_date"]
    assert restored["message_text"] == "current generation"
    assert restored["recovered_initial_notification_pending_at"] == (
        scanned_at.isoformat()
    )


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


def test_final_report_uses_exact_three_accounts_and_ignores_extra_registry() -> None:
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
        "xflarxx_primary",
    }
    text, _ = auto_participation_notifications._result_message(
        "wheel", {"identifier": "wheel"}, accounts
    )
    assert "Аккаунт 1" in text
    assert "Аккаунт 2" in text
    assert "Резервный аккаунт" not in text
    assert "xFLARXx" in text


def test_referral_success_on_one_account_sends_one_honest_owner_result() -> None:
    base = "evt:1393d78ddd6274d3103d"
    item = {
        "wheel_key": "kekw2",
        "identifier": "kekw2",
        "source": "shadowkekw",
        "event_id": "1393d78ddd6274d3103d",
        "message_text": "Колесико для рефов",
        "referral_restricted": True,
        "wheel_type": wheel_publications_v2.WHEEL_TYPE_REFERRAL,
        "referral_classification_evidence": (
            wheel_publications_v2.STRONG_REFERRAL_EVIDENCE
        ),
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
            base + "#account:xflarxx_primary": {
                "wheel_key": "kekw2",
                "event_token": base,
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "status": "referral_ineligible",
                "detail": (
                    "referral_ineligible_exact_text:main:"
                    "Ваш аккаунт не является рефералом"
                ),
                "bot_failure_pending_at": "2026-07-30T15:00:20+00:00",
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
    assert "Реферальное колесо" in text
    assert "Источник: @shadowkekw" in text
    assert "⚠️ Аккаунт 1 — результат не подтверждён" in text
    assert "✅ Аккаунт 2 — участие подтверждено BetBoom" in text
    assert "⛔ xFLARXx — недоступно" in text
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
    assert auto_participation_notifications._should_send_event_result(
        {"notification_preferences": {}},
        item,
        {
            key: (token, record, False)
            for key, (token, record, _success) in accounts.items()
        },
    )


def test_referral_outcomes_are_independent_for_all_three_accounts() -> None:
    accounts = {
        "vyacheslav_primary": (
            "evt:one",
            {
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "status": "participated",
            },
            True,
        ),
        "vyacheslav_secondary": (
            "evt:one#account:vyacheslav_secondary",
            {
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "status": "referral_ineligible",
                "detail": "referral_ineligible_exact_text:main:не является рефералом",
            },
            False,
        ),
        "xflarxx_primary": (
            "evt:one#account:xflarxx_primary",
            {
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "status": "referral_ineligible",
                "detail": "referral_ineligible_exact_text:main:не является рефералом",
            },
            False,
        ),
    }

    text, _ = auto_participation_notifications._result_message(
        "ref-one",
        {"identifier": "ref-one", "message_text": "Колесо для рефов"},
        accounts,
    )
    assert text.count("участие подтверждено BetBoom") == 1
    assert text.count("BetBoom подтвердил реферальное ограничение") == 2

    accounts["vyacheslav_secondary"] = (
        "evt:one#account:vyacheslav_secondary",
        {
            "account_key": "vyacheslav_secondary",
            "account_label": "Аккаунт 2",
            "status": "participated",
        },
        True,
    )
    text, _ = auto_participation_notifications._result_message(
        "ref-two",
        {"identifier": "ref-two", "message_text": "Колесо для рефов"},
        accounts,
    )
    assert text.count("участие подтверждено BetBoom") == 2
    assert text.count("BetBoom подтвердил реферальное ограничение") == 1


def test_telegram_referral_hint_stays_normal_and_account_results_stay_cautious() -> None:
    item = {
        "identifier": "hint",
        "message_text": "Реферальный розыгрыш BetBoom",
    }
    assert (
        wheel_publications_v2.referral_classification(item)
        == wheel_publications_v2.WHEEL_TYPE_NORMAL
    )
    accounts = {
        "vyacheslav_primary": (
            "evt:hint",
            {
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "status": "technical_error",
                "detail": "TimeoutError: page timed out",
            },
            False,
        ),
        "vyacheslav_secondary": (
            "evt:hint#account:vyacheslav_secondary",
            {
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "status": "unconfirmed",
            },
            False,
        ),
        "xflarxx_primary": (
            "evt:hint#account:xflarxx_primary",
            {
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "status": "participated",
            },
            True,
        ),
    }
    text, _ = auto_participation_notifications._result_message(
        "hint", item, accounts
    )
    assert "Предположительно реферальное колесо" not in text
    assert "реферальное колесо" not in text.casefold()
    assert "🛠 Аккаунт 1 — техническая ошибка" in text
    assert "⚠️ Аккаунт 2 — результат не подтверждён" in text
    assert "✅ xFLARXx" in text

    ordinary = {"identifier": "ordinary", "message_text": "Колесо для всех"}
    ordinary_text, _ = auto_participation_notifications._result_message(
        "ordinary", ordinary, accounts
    )
    assert wheel_publications_v2.referral_classification(ordinary) == "normal"
    assert "реферальное колесо" not in ordinary_text.casefold()


def test_identifier_history_does_not_classify_later_generation() -> None:
    state = {
        "auto_participation_events": {
            "evt:old": {
                "wheel_key": "kekw2",
                "status": "unconfirmed",
                "event_context": {
                    "wheel_key": "kekw2",
                    "identifier": "kekw2",
                    "message_date": "2026-07-30T12:45:41+00:00",
                    "message_text": "Колесико для рефов",
                    "referral_restricted": True,
                    "wheel_type": "referral",
                },
            }
        },
        "referral_identifier_history": {
            "kekw2": {
                "identifier": "kekw2",
                "classification": "referral",
                "evidence": "legacy_identifier_history",
                "last_seen_at": "2026-07-30T12:45:41+00:00",
            }
        },
    }
    current = {
        "wheel_key": "kekw2",
        "identifier": "kekw2",
        "message_text": "ВСЕ В КОЛЕСО",
    }

    classification = wheel_publications_v2.apply_referral_context(
        state,
        current,
        observed_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
    )

    assert classification == wheel_publications_v2.WHEEL_TYPE_NORMAL
    assert current["wheel_type"] == wheel_publications_v2.WHEEL_TYPE_NORMAL
    assert current.get("referral_restricted") is not True
    assert current.get("referral_suspected") is not True
    assert "referral_classification_evidence" not in current
    assert state["referral_identifier_history"]["kekw2"]["classification"] == "referral"


def test_page_banner_hint_does_not_classify_without_account_refusal() -> None:
    state: dict = {}
    current = {
        "wheel_key": "banner-wheel",
        "identifier": "banner-wheel",
        "message_text": "Колесо для всех",
    }
    detail = (
        "page_referral_hint=referral;page_explicit_referral_restriction; "
        "кнопка участия не найдена"
    )

    classification = wheel_publications_v2.apply_referral_context(
        state,
        current,
        browser_detail=detail,
    )

    assert classification == wheel_publications_v2.WHEEL_TYPE_NORMAL
    assert current["wheel_type"] == wheel_publications_v2.WHEEL_TYPE_NORMAL
    assert current.get("referral_restricted") is not True
    assert "referral_classification_evidence" not in current


def test_new_action_does_not_inherit_referral_from_old_generation(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
    monkeypatch.setattr(monitor, "now_utc", lambda: now)
    state = {
        "active_wheels": {
            "kekw2": {
                "wheel_key": "kekw2",
                "identifier": "kekw2",
                "action_id": 1124,
                "server_start_at": "2026-07-30T14:53:33.479000+00:00",
                "message_text": "Колесико для рефов",
                "referral_restricted": True,
                "wheel_type": "referral",
                "referral_classification_evidence": (
                    wheel_publications_v2.STRONG_REFERRAL_EVIDENCE
                ),
            }
        },
        "participating_wheels": {},
    }
    message = monitor.Message(
        source="burdakekw",
        message_id=5542,
        date=now,
        text="ВСЕ В КОЛЕСО",
        message_url="https://telegram.me/burdakekw/5542",
    )

    monitor.remember_active_wheel(
        state,
        message,
        "https://betboom.ru/freestream/kekw2",
        now,
        "active",
        "api",
        action_id=1156,
        server_start_at=datetime(2026, 8, 1, 11, 19, 30, 763000, tzinfo=UTC),
    )

    current = state["active_wheels"]["kekw2"]
    assert current["wheel_type"] == wheel_publications_v2.WHEEL_TYPE_NORMAL
    assert current.get("referral_restricted") is not True
    assert current.get("referral_suspected") is not True
    assert "referral_classification_evidence" not in current


def test_initial_notification_ignores_identifier_history_referral_hint(monkeypatch) -> None:
    now = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
    monkeypatch.setattr(monitor, "now_utc", lambda: now)
    delivered: list[str] = []
    monkeypatch.setattr(
        monitor,
        "send_message",
        lambda text, **_kwargs: delivered.append(text) or {"ok": True},
    )
    monkeypatch.setattr(
        monitor,
        "dispatch_notified_wheel_event",
        lambda _state, _link: True,
    )
    state = {
        "active_wheels": {},
        "button_contexts": {},
        "participating_wheels": {},
        "auto_participation_events": {
            "evt:old": {
                "wheel_key": "kekw2",
                "event_context": {
                    "wheel_key": "kekw2",
                    "identifier": "kekw2",
                    "message_text": "Колесико для рефов",
                    "referral_restricted": True,
                },
            }
        },
    }
    message = monitor.Message(
        source="burdakekw",
        message_id=5542,
        date=now,
        text="ВСЕ В КОЛЕСО",
        message_url="https://telegram.me/burdakekw/5542",
    )

    monitor.notify_new_link(
        message,
        "https://betboom.ru/freestream/kekw2",
        now,
        "api",
        [],
        state,
        action_id=1156,
        server_start_at=datetime(2026, 8, 1, 11, 19, 30, 763000, tzinfo=UTC),
    )

    assert len(delivered) == 1
    assert "Предположительно реферальное колесо" not in delivered[0]
    assert "Реферальное колесо" not in delivered[0]


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
