from __future__ import annotations

from datetime import datetime, timezone

import auto_participation_notifications as notifications
import auto_participation_recovery as recovery
import betboom_account_participation as account2
import betboom_auto_participation as auto
import betboom_join_outcomes as outcomes
import betboom_participation_browser as browser
import personal_reminder_filter
from bbvg.storage import canonical_account_status


class _Page:
    url = "https://betboom.ru/freestream/stale-wheel"


def _closed_record(account_key: str, account_label: str) -> dict:
    detail = (
        "expired_exact_text:Колесо завершено; "
        "колесо уже завершилось до подтверждения участия"
    )
    return {
        "wheel_key": "stale-wheel",
        "event_token": "evt:inactive-wheel",
        "account_key": account_key,
        "account_label": account_label,
        "status": "participation_closed",
        "bot_failure_status": "participation_closed",
        "detail": detail,
        "bot_failure_detail": detail,
        "retry_allowed": False,
    }


def test_active_api_wheel_with_frontend_404_stays_retryable(monkeypatch) -> None:
    monkeypatch.setattr(browser, "_diagnostic_labels", lambda _page: "main:Перейти на главную")
    monkeypatch.setattr(browser, "_save_diagnostics", lambda *_args, **_kwargs: "/tmp/inactive-wheel")

    result = browser._missing_participation_control_failure(
        _Page(),
        "https://betboom.ru/freestream/stale-wheel",
        preparations=["main:Окей"],
    )

    assert result.success is False
    assert result.status == "button_not_found"
    assert "transient_page_state:participation_control_absent_after_reload" in result.detail
    assert "Перейти на главную" in result.detail
    assert result.artifact_url == "/tmp/inactive-wheel"
    assert canonical_account_status(result.status, "", result.detail) == "unconfirmed"
    active_event = {"api_status": "active"}
    durable = recovery._failure_record(
        None,
        key="stale-wheel",
        status=result.status,
        detail=result.detail,
        scanned_at=datetime(2026, 9, 10, 13, 57, tzinfo=timezone.utc),
    )
    assert active_event["api_status"] == "active"
    assert durable["retry_allowed"] is True


def test_legacy_missing_control_false_terminal_is_rearmed() -> None:
    previous = {
        "status": "participation_closed",
        "detail": (
            "expired_exact_state:participation_control_absent_after_reload; "
            "кнопка участия отсутствует"
        ),
        "attempt_version": auto._PARTICIPATION_ATTEMPT_VERSION,
    }

    assert outcomes.legacy_missing_control_was_misclassified(previous)
    assert account2._should_attempt(previous, datetime.now(timezone.utc))
    assert personal_reminder_filter._recoverable_processed_failure(
        previous,
        {"auto_participation_status": "participation_closed"},
    ) == "legacy_missing_control_false_terminal"

    class _Monitor:
        WHEEL_VERIFICATION_FAILED = "failed"

        @staticmethod
        def parse_datetime(_value):
            return None

    events = {"evt:active": previous}
    entry = {"url": "https://betboom.ru/freestream/stale-wheel"}
    rearmed, _notified, _notified_at = auto._rearm_legacy_button_not_found(
        events,
        "evt:active",
        entry,
        _Monitor,
        datetime.now(timezone.utc),
    )

    assert rearmed is True
    assert "evt:active" not in events
    assert entry["auto_participation_rearm_reason"] == (
        "legacy_missing_control_false_terminal"
    )


def test_all_accounts_missing_control_reports_wheel_already_finished() -> None:
    accounts = {
        notifications.PRIMARY_ACCOUNT_KEY: (
            "evt:inactive-wheel",
            _closed_record(
                notifications.PRIMARY_ACCOUNT_KEY,
                notifications.PRIMARY_ACCOUNT_LABEL,
            ),
            False,
        ),
        notifications.SECONDARY_ACCOUNT_KEY: (
            "evt:inactive-wheel#account:vyacheslav_secondary",
            _closed_record(
                notifications.SECONDARY_ACCOUNT_KEY,
                notifications.SECONDARY_ACCOUNT_LABEL,
            ),
            False,
        ),
        notifications.XFLARXX_ACCOUNT_KEY: (
            "evt:inactive-wheel#account:xflarxx_primary",
            _closed_record(
                notifications.XFLARXX_ACCOUNT_KEY,
                notifications.XFLARXX_ACCOUNT_LABEL,
            ),
            False,
        ),
    }

    text, _markup = notifications._result_message(
        "stale-wheel",
        {"identifier": "stale-wheel", "source": "some_streamer"},
        accounts,
    )

    assert "⌛ <b>Колесо уже завершилось</b>" in text
    assert "⌛ Аккаунт 1 — колесо уже завершилось" in text
    assert "⌛ Аккаунт 2 — колесо уже завершилось" in text
    assert "⌛ xFLARXx — колесо уже завершилось" in text


def test_missing_button_without_stable_reload_proof_stays_unconfirmed() -> None:
    assert canonical_account_status(
        "button_not_found",
        "",
        "кнопка не найдена из-за неизвестного состояния страницы",
    ) == "unconfirmed"
