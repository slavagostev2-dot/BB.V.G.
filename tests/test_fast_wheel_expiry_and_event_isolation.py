from __future__ import annotations

from datetime import datetime, timezone

import auto_participation_notifications as notifications
import auto_participation_owner_sync
import betboom_participation_browser as browser

UTC = timezone.utc


class _BodyLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout: int = 0) -> str:
        return self.text


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text

    def locator(self, selector: str):
        if selector == "body":
            return _BodyLocator(self.text)
        raise AssertionError(f"unexpected selector: {selector}")


def _failure_record(
    *,
    event_token: str,
    account_key: str,
    account_label: str,
    attempted_at: str,
) -> dict:
    detail = (
        "expired_exact_text:main:Пока ждёшь следующий запуск, заглядывай в другие акции; "
        "колесо уже завершилось до подтверждения участия; clicked_by_bot=false"
    )
    return {
        "wheel_key": "zonertw4",
        "event_token": event_token,
        "account_key": account_key,
        "account_label": account_label,
        "status": "participation_closed",
        "bot_failure_status": "participation_closed",
        "detail": detail,
        "bot_failure_detail": detail,
        "attempted_at": attempted_at,
        "bot_failure_pending_at": attempted_at,
        "retry_allowed": False,
    }


def test_zonertw4_waiting_for_next_launch_is_terminal_closed_evidence(monkeypatch) -> None:
    page = _Page(
        "ZONER КОЛЕСО ФРИБЕТОВ "
        "Пока ждёшь следующий запуск, заглядывай в другие акции "
        "Об акции Другие акции"
    )
    evidence = browser._wheel_closed_evidence(page)
    assert "следующий запуск" in evidence

    monkeypatch.setattr(browser, "_save_diagnostics", lambda *_args, **_kwargs: "")
    result = browser._wheel_closed_failure(
        page,
        "https://betboom.ru/freestream/zonertw4",
        evidence,
    )
    assert result.success is False
    assert result.status == "participation_closed"
    assert "expired_exact_text:" in result.detail


def test_old_zonertw4_success_cannot_attach_to_current_generation(monkeypatch) -> None:
    item = {
        "wheel_key": "zonertw4",
        "identifier": "zonertw4",
        "source": "private_2445382077",
        "action_id": 2100,
        "server_start_at": "2026-09-08T09:58:21.335000+00:00",
        "message_date": "2026-09-08T09:58:30+00:00",
        "url": "https://betboom.ru/freestream/zonertw4",
    }
    current = auto_participation_owner_sync._event_token(item, "zonertw4")
    old = "evt:288cf62d4fd4b339c368"

    events = {
        current: _failure_record(
            event_token=current,
            account_key=notifications.PRIMARY_ACCOUNT_KEY,
            account_label=notifications.PRIMARY_ACCOUNT_LABEL,
            attempted_at="2026-09-08T09:59:41+00:00",
        ),
        current + "#account:vyacheslav_secondary": _failure_record(
            event_token=current,
            account_key=notifications.SECONDARY_ACCOUNT_KEY,
            account_label=notifications.SECONDARY_ACCOUNT_LABEL,
            attempted_at="2026-09-08T09:59:53+00:00",
        ),
        current + "#account:xflarxx_primary": _failure_record(
            event_token=current,
            account_key=notifications.XFLARXX_ACCOUNT_KEY,
            account_label=notifications.XFLARXX_ACCOUNT_LABEL,
            attempted_at="2026-09-08T10:00:06+00:00",
        ),
        old + "#account:vyacheslav_secondary": {
            "wheel_key": "zonertw4",
            "event_token": old,
            "account_key": notifications.SECONDARY_ACCOUNT_KEY,
            "account_label": notifications.SECONDARY_ACCOUNT_LABEL,
            "status": "participated",
            "attempted_at": "2026-08-07T11:32:39+00:00",
            "bot_success_pending_at": "2026-08-07T11:32:39+00:00",
        },
        old + "#account:xflarxx_primary": {
            "wheel_key": "zonertw4",
            "event_token": old,
            "account_key": notifications.XFLARXX_ACCOUNT_KEY,
            "account_label": notifications.XFLARXX_ACCOUNT_LABEL,
            "status": "participated",
            "attempted_at": "2026-08-07T11:32:44+00:00",
            "bot_success_pending_at": "2026-08-07T11:32:44+00:00",
        },
    }
    state = {
        "active_wheels": {"zonertw4": item},
        "auto_participation_events": events,
    }

    assert notifications._canonical_event_token(
        state,
        old + "#account:vyacheslav_secondary",
        events[old + "#account:vyacheslav_secondary"],
    ) == old

    # The owner-sync grace period is tested elsewhere. Here all current terminal
    # failures are made eligible so this regression focuses on generation identity.
    current_failures = [
        (token, record)
        for token, record in events.items()
        if token == current or token.startswith(current + "#account:")
    ]
    monkeypatch.setattr(
        auto_participation_owner_sync,
        "pending_failure_events",
        lambda _state, now=None: current_failures,
    )

    groups = notifications._settled_event_groups(
        state,
        now=datetime(2026, 9, 8, 10, 5, tzinfo=UTC),
    )
    assert current in groups
    accounts = groups[current]
    assert set(accounts) == {
        notifications.PRIMARY_ACCOUNT_KEY,
        notifications.SECONDARY_ACCOUNT_KEY,
        notifications.XFLARXX_ACCOUNT_KEY,
    }
    assert all(success is False for _token, _record, success in accounts.values())
    assert all(
        notifications._account_result_status(record, success) == "expired"
        for _token, record, success in accounts.values()
    )

    text, _markup = notifications._result_message("zonertw4", item, accounts)
    assert "⌛ <b>Колесо уже завершилось</b>" in text
    assert "⌛ Аккаунт 1 — колесо уже завершилось" in text
    assert "⌛ Аккаунт 2 — колесо уже завершилось" in text
    assert "⌛ xFLARXx — колесо уже завершилось" in text
    assert "✅ Аккаунт 2" not in text
    assert "✅ xFLARXx" not in text


def test_partial_success_does_not_claim_whole_wheel_expired() -> None:
    accounts = {
        notifications.PRIMARY_ACCOUNT_KEY: (
            "evt:one",
            {
                "account_key": notifications.PRIMARY_ACCOUNT_KEY,
                "account_label": notifications.PRIMARY_ACCOUNT_LABEL,
                "status": "participated",
            },
            True,
        ),
        notifications.SECONDARY_ACCOUNT_KEY: (
            "evt:one#account:vyacheslav_secondary",
            _failure_record(
                event_token="evt:one",
                account_key=notifications.SECONDARY_ACCOUNT_KEY,
                account_label=notifications.SECONDARY_ACCOUNT_LABEL,
                attempted_at="2026-09-08T10:00:00+00:00",
            ),
            False,
        ),
        notifications.XFLARXX_ACCOUNT_KEY: (
            "evt:one#account:xflarxx_primary",
            _failure_record(
                event_token="evt:one",
                account_key=notifications.XFLARXX_ACCOUNT_KEY,
                account_label=notifications.XFLARXX_ACCOUNT_LABEL,
                attempted_at="2026-09-08T10:00:01+00:00",
            ),
            False,
        ),
    }
    text, _markup = notifications._result_message(
        "zonertw4",
        {"identifier": "zonertw4"},
        accounts,
    )
    assert "Автоучастие выполнено не полностью" in text
    assert "<b>Колесо уже завершилось</b>" not in text
