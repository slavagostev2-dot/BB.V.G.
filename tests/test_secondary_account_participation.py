from __future__ import annotations

from datetime import datetime, timezone

import betboom_account_participation as account2
import secondary_account_participation as runner


UTC = timezone.utc


def test_account_configs_preserve_existing_contract() -> None:
    second = runner.account_config("account2")
    third = runner.account_config("account3")

    assert second.account_key == "vyacheslav_secondary"
    assert second.account_owner == "vyacheslav"
    assert second.account_order == 20
    assert second.multi_account_version == 1
    assert second.last_run_state_field == "last_secondary_account_participation_at"
    assert second.ensure_default_registry is True
    assert second.canonicalize_primary_aliases is True

    assert third.account_key == "xflarxx_primary"
    assert third.account_owner == "xflarxx"
    assert third.account_order == 10
    assert third.multi_account_version == 2
    assert third.last_run_state_field == "last_xflarxx_account_participation_at"
    assert third.ensure_default_registry is False
    assert third.canonicalize_primary_aliases is False


def test_generic_runner_preserves_success_record_contract(monkeypatch) -> None:
    state: dict[str, object] = {
        "active_wheels": {},
        "auto_participation_events": {},
    }
    saved: list[dict[str, object]] = []
    calls = {"ensure": 0, "canonicalize": 0, "register": []}
    current = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)

    config = runner.AccountRunConfig(
        name="test",
        account_key="test_account",
        account_owner="test_owner",
        account_order=30,
        multi_account_version=7,
        last_run_state_field="last_test_account_at",
        session_getter=lambda: {"cookies": [], "origins": []},
        label_getter=lambda: "Тестовый аккаунт",
        alert_user_getter=lambda: "Тестовый пользователь",
        missing_session_error="missing",
        ensure_default_registry=True,
        canonicalize_primary_aliases=True,
    )

    monkeypatch.setattr(account2, "_load_json", lambda path, default: state)
    monkeypatch.setattr(
        account2,
        "_candidate_rows",
        lambda state_value, path: [
            {
                "wheel_key": "wheel",
                "identifier": "wheel",
                "url": "https://betboom.ru/freestream/wheel",
            }
        ],
    )
    monkeypatch.setattr(account2, "_should_attempt", lambda previous, now: True)
    monkeypatch.setattr(
        account2,
        "_base_event_token",
        lambda item, wheel_key="": "evt:test",
    )
    monkeypatch.setattr(
        account2,
        "_participate_with_storage",
        lambda url, session: account2.primary_auto.ParticipationResult(
            True,
            "participated",
            "BetBoom подтвердил участие после нажатия (exact_success_label)",
            "artifact",
        ),
    )
    monkeypatch.setattr(runner.monitor, "now_utc", lambda: current)
    monkeypatch.setattr(
        runner.monitor,
        "save_state",
        lambda value: saved.append(dict(value)),
    )
    monkeypatch.setattr(
        runner.wheel_publications_v2,
        "apply_referral_context",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        account2.primary_auto,
        "ensure_default_account_registry",
        lambda value: calls.__setitem__("ensure", calls["ensure"] + 1),
    )
    monkeypatch.setattr(
        account2.primary_auto,
        "canonicalize_primary_event_aliases",
        lambda value: calls.__setitem__(
            "canonicalize", calls["canonicalize"] + 1
        ),
    )
    monkeypatch.setattr(
        account2.primary_auto,
        "register_account",
        lambda value, **kwargs: calls["register"].append(kwargs),
    )
    monkeypatch.setattr(
        account2.primary_auto,
        "merge_event_record",
        lambda previous, record: dict(record),
    )

    summary = runner.run_configured_account(config)

    assert calls["ensure"] == 1
    assert calls["canonicalize"] == 1
    assert calls["register"] == [
        {
            "account_key": "test_account",
            "account_label": "Тестовый аккаунт",
            "account_owner": "test_owner",
            "account_order": 30,
        }
    ]
    assert summary == {
        "account_key": "test_account",
        "account_label": "Тестовый аккаунт",
        "alert_user": "Тестовый пользователь",
        "attempted": 1,
        "succeeded": 1,
        "terminal_failed": 0,
        "deferred": 0,
        "skipped": 0,
    }

    event = state["auto_participation_events"]["evt:test#account:test_account"]
    assert event["status"] == "participated"
    assert event["event_token"] == "evt:test"
    assert event["account_key"] == "test_account"
    assert event["account_owner"] == "test_owner"
    assert event["account_order"] == 30
    assert event["multi_account_version"] == 7
    assert event["bot_success_sync_status"] == "waiting_for_control_center"
    assert event["artifact_url"] == "artifact"
    assert state["last_test_account_at"] == current.isoformat()
    assert len(saved) == 1
