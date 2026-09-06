from __future__ import annotations

import auto_participation_worker as worker
import betboom_auto_participation as auto
import betboom_participation_browser as browser


def test_primary_worker_uses_exact_browser_and_restores_functions(monkeypatch) -> None:
    original_participate = auto.participate
    original_notify = auto._notify_manual_participation
    observed: dict[str, object] = {}

    def process(state, monitor):
        observed["participate"] = auto.participate
        observed["notify"] = auto._notify_manual_participation
        observed["state"] = state
        observed["monitor"] = monitor
        return {"changed": False, "attempted": 1, "succeeded": 0, "failed": 1}

    monkeypatch.setattr(auto, "process_new_wheel_events", process)
    state: dict[str, object] = {}
    monitor = object()

    result = worker._run_exact_primary_attempt(state, monitor)

    assert observed["participate"] is browser.participate
    assert observed["notify"] is worker._defer_failure_notification
    assert observed["state"] is state
    assert observed["monitor"] is monitor
    assert result["attempted"] == 1
    assert auto.participate is original_participate
    assert auto._notify_manual_participation is original_notify


def test_failure_notification_remains_deferred_to_recovery() -> None:
    assert worker._defer_failure_notification(object(), {}, object()) == (
        False,
        "deferred_to_recovery",
    )


def _active_state(record: dict[str, object]) -> tuple[dict[str, object], str]:
    entry: dict[str, object] = {
        "identifier": "wheel-a",
        "url": "https://betboom.ru/freestream/wheel-a",
        "action_id": 1722,
        "server_start_at": "2026-09-06T09:27:54.141000+00:00",
        "auto_participation_status": "participated",
        "auto_participation_confirmed_at": "2026-09-06T09:28:00+00:00",
    }
    token = auto._event_token("wheel-a", entry)
    state: dict[str, object] = {
        "active_wheels": {"wheel-a": entry},
        "auto_participation_events": {token: record},
    }
    return state, token


def test_changed_primary_profile_rearms_old_success() -> None:
    state, token = _active_state(
        {
            "status": "participated",
            "account_key": auto.PRIMARY_ACCOUNT_KEY,
            "auth_revision": "auth:v1:old-profile",
        }
    )

    changed, rearmed, baselines = worker._prepare_primary_auth_revision(
        state,
        "auth:v1:new-profile",
    )

    assert changed is True
    assert rearmed == 1
    assert baselines == 0
    assert token not in state["auto_participation_events"]
    entry = state["active_wheels"]["wheel-a"]
    assert "auto_participation_status" not in entry
    assert "auto_participation_confirmed_at" not in entry
    assert entry["auto_participation_auth_rearm_reason"] == (
        "betboom_profile_revision_changed"
    )


def test_same_primary_profile_keeps_success() -> None:
    state, token = _active_state(
        {
            "status": "participated",
            "account_key": auto.PRIMARY_ACCOUNT_KEY,
            "auth_revision": "auth:v1:same-profile",
        }
    )

    changed, rearmed, baselines = worker._prepare_primary_auth_revision(
        state,
        "auth:v1:same-profile",
    )

    assert changed is False
    assert rearmed == 0
    assert baselines == 0
    assert token in state["auto_participation_events"]


def test_legacy_primary_baseline_is_stamped_without_click_rearm() -> None:
    state, token = _active_state(
        {
            "status": "baseline_existing",
            "account_key": auto.PRIMARY_ACCOUNT_KEY,
        }
    )

    changed, rearmed, baselines = worker._prepare_primary_auth_revision(
        state,
        "auth:v1:current-profile",
    )

    assert changed is True
    assert rearmed == 0
    assert baselines == 1
    assert state["auto_participation_events"][token]["auth_revision"] == (
        "auth:v1:current-profile"
    )