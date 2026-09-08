from __future__ import annotations

import auto_participation_notifications as notifications
import betboom_participation_browser as browser
from bbvg.storage import canonical_account_status


class _Page:
    url = "https://betboom.ru/freestream/stale-wheel"


def _closed_record(account_key: str, account_label: str) -> dict:
    detail = (
        "expired_exact_state:participation_control_absent_after_reload; "
        "кнопка участия отсутствует после ожидания и повторной загрузки"
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


def test_stable_missing_participation_control_is_terminal_closed(monkeypatch) -> None:
    monkeypatch.setattr(browser, "_diagnostic_labels", lambda _page: "main:Об акции | main:Другие акции")
    monkeypatch.setattr(browser, "_save_diagnostics", lambda *_args, **_kwargs: "/tmp/inactive-wheel")

    result = browser._missing_participation_control_failure(
        _Page(),
        "https://betboom.ru/freestream/stale-wheel",
        preparations=["main:Окей"],
    )

    assert result.success is False
    assert result.status == "participation_closed"
    assert "expired_exact_state:participation_control_absent_after_reload" in result.detail
    assert result.artifact_url == "/tmp/inactive-wheel"
    assert canonical_account_status(result.status, "", result.detail) == "expired"


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
