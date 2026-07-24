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
