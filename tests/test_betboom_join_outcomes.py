from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import betboom_account_participation as account2
import betboom_join_outcomes as outcomes
import xflarxx_account_participation as account3


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "betboom_join_promo_code_not_used.json"
)
UTC = timezone.utc


def test_real_promo_code_refusal_is_terminal_and_not_retryable() -> None:
    event = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = outcomes.classify_join_response_event(event)

    assert result is not None
    assert result.observed is True
    assert result.success is False
    assert result.status == "ineligible_promo_code"
    assert result.reason == "promo_code_not_used"
    assert result.terminal is True
    assert result.retry_allowed is False
    assert "промокоду стримера" in result.message.casefold()


def test_latest_join_outcome_ignores_unrelated_network_events() -> None:
    event = json.loads(FIXTURE.read_text(encoding="utf-8"))
    trace = [
        {
            "kind": "response",
            "method": "GET",
            "url": "https://betboom.ru/api/streamer-wheel/action/info",
            "status": 200,
            "body": {"code": 200, "status": "OK"},
        },
        event,
    ]
    result = outcomes.latest_join_outcome(trace)
    assert result is not None
    assert result.status == "ineligible_promo_code"


def test_unknown_business_reason_is_preserved_without_guessing() -> None:
    event = {
        "kind": "response",
        "method": "POST",
        "url": "https://betboom.ru/api/streamer-wheel/action/join",
        "status": 200,
        "body": {
            "code": 400,
            "status": "BAD_REQUEST",
            "reason": "brand_new_reason",
            "error": {"message": "New BetBoom rule"},
        },
    }
    result = outcomes.classify_join_response_event(event)
    assert result is not None
    assert result.status == "unknown_betboom_error"
    assert result.reason == "brand_new_reason"
    assert result.retry_allowed is False


def test_server_error_is_retryable() -> None:
    event = {
        "kind": "response",
        "method": "POST",
        "url": "https://betboom.ru/api/streamer-wheel/action/join",
        "status": 503,
        "body": {"code": 503, "status": "ERROR"},
    }
    result = outcomes.classify_join_response_event(event)
    assert result is not None
    assert result.status == "server_error"
    assert result.terminal is False
    assert result.retry_allowed is True


def test_retry_policy_stops_promo_refusal_but_keeps_server_error() -> None:
    now = datetime(2026, 9, 8, tzinfo=UTC)
    assert not account2._should_attempt(
        {"status": "ineligible_promo_code"}, now
    )
    assert account2._should_attempt(
        {
            "status": "server_error",
            "retry_after_at": "2026-09-07T00:00:00+00:00",
        },
        now,
    )


def test_account2_notification_contains_exact_terminal_reason() -> None:
    text, _markup = account2._short_message(
        False,
        "rewsa",
        {"identifier": "REWSA"},
        {
            "account_label": "Аккаунт 2",
            "status": "ineligible_promo_code",
            "bot_failure_detail": (
                "BetBoom отказал в участии: Акция доступна только при "
                "регистрации по промокоду стримера"
            ),
        },
    )
    assert "промокод" in text.casefold()
    assert "повторной автоматической попытки" in text.casefold()


def test_xflarxx_notification_contains_exact_terminal_reason() -> None:
    text, _markup = account3._message(
        "rewsa",
        {"identifier": "REWSA"},
        {
            "account_label": "xFLARXx",
            "status": "ineligible_promo_code",
            "bot_failure_detail": (
                "BetBoom отказал в участии: Акция доступна только при "
                "регистрации по промокоду стримера"
            ),
        },
        False,
    )
    assert "промокод" in text.casefold()
    assert "повторной автоматической попытки" in text.casefold()


def test_event_summary_marks_all_accounts_terminal_only_when_all_registered_failed() -> None:
    item = {
        "wheel_key": "rewsa",
        "identifier": "REWSA",
        "action_id": 2047,
        "server_start_at": "2026-09-07T19:03:00.406000+00:00",
    }
    from bbvg.storage import event_id_from_entry

    event_id = event_id_from_entry(item, wheel_key="rewsa")
    state = {
        "active_wheels": {"rewsa": dict(item)},
        "auto_participation_account_registry": {
            "vyacheslav_primary": {"enabled": True},
            "vyacheslav_secondary": {"enabled": True},
            "xflarxx_primary": {"enabled": True},
        },
        "auto_participation_events": {
            event_id: {
                "event_token": event_id,
                "account_key": "vyacheslav_primary",
                "status": "ineligible_promo_code",
                "detail": "promo",
            },
            f"{event_id}#account:vyacheslav_secondary": {
                "event_token": event_id,
                "account_key": "vyacheslav_secondary",
                "status": "ineligible_promo_code",
                "detail": "promo",
            },
            f"{event_id}#account:xflarxx_primary": {
                "event_token": event_id,
                "account_key": "xflarxx_primary",
                "status": "ineligible_promo_code",
                "detail": "promo",
            },
        },
    }

    summary = outcomes.finalize_event_account_summary(state, item)
    assert summary["all_accounts_settled"] is True
    assert summary["all_accounts_terminal_failure"] is True
    active = state["active_wheels"]["rewsa"]
    assert active["auto_participation_terminal"] is True
    assert (
        active["auto_participation_terminal_reason"]
        == "all_accounts_terminal_failure"
    )
