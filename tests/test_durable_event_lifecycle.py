from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import auto_participation_dispatch
import auto_participation_bot_sync
import betboom_participation_browser
import monitor
import personal_reminder_filter
from bbvg.storage import EventStore
from bbvg.storage.event_payload import materialize_event_payload

UTC = timezone.utc


def _entry() -> dict[str, object]:
    return {
        "wheel_key": "deko2",
        "identifier": "deko2",
        "url": "https://betboom.ru/freestream/deko2",
        "source": "kolesaBB",
        "message_id": 260,
        "message_date": "2026-07-25T15:15:00+00:00",
        "message_url": "https://t.me/kolesaBB/260",
        "action_id": 1050,
        "server_start_at": "2026-07-25T15:15:03.147+00:00",
        "verification_status": "confirmed",
        "status": "active",
    }


def _store(tmp_path: Path) -> EventStore:
    return EventStore(
        tmp_path / "events.sqlite3",
        audit_path=tmp_path / "events.jsonl",
        notification_audit_path=tmp_path / "notifications.jsonl",
    )


def test_notification_is_not_attempted_when_durable_prepare_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStore:
        def prepare_event(self, *args, **kwargs):
            raise OSError("disk unavailable")

    sent: list[str] = []
    monkeypatch.setattr(monitor, "event_store", lambda: BrokenStore())
    monkeypatch.setattr(monitor, "send_message", lambda *args, **kwargs: sent.append("sent"))
    monkeypatch.setattr(monitor, "save_state", lambda state: None)
    state: dict[str, object] = {"active_wheels": {}, "participating_wheels": {}}
    message = monitor.Message(
        "kolesaBB",
        260,
        datetime(2026, 7, 25, 15, 15, tzinfo=UTC),
        "https://betboom.ru/freestream/deko2",
        "https://t.me/kolesaBB/260",
    )

    with pytest.raises(OSError, match="disk unavailable"):
        monitor.notify_activation(
            message,
            str(_entry()["url"]),
            datetime(2026, 7, 25, 15, 45, tzinfo=UTC),
            "api",
            [],
            state,
            action_id=1050,
            verification_status="confirmed",
            server_start_at=datetime(
                2026,
                7,
                25,
                15,
                15,
                3,
                147000,
                tzinfo=UTC,
            ),
        )
    assert sent == []


def test_durable_event_and_dispatch_precede_telegram(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    order: list[str] = []
    original_prepare = store.prepare_event

    def prepare(*args, **kwargs):
        order.append("durable_prepare")
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(store, "prepare_event", prepare)
    monkeypatch.setattr(monitor, "event_store", lambda: store)
    monkeypatch.setattr(monitor, "save_state", lambda state: order.append("state_snapshot"))
    monkeypatch.setattr(
        monitor,
        "process_auto_participation_dispatch",
        lambda state: order.append("dispatch_wake") or True,
    )
    monkeypatch.setattr(
        monitor,
        "send_message",
        lambda *args, **kwargs: (
            order.append("telegram_send")
            or {
                "ok": True,
                "result": {
                    "deliveries": [
                        {
                            "recipient_scope": "recipient:test",
                            "status": "sent",
                            "telegram_message_id": 99,
                        }
                    ]
                },
            }
        ),
    )
    state: dict[str, object] = {"active_wheels": {}, "participating_wheels": {}}
    message = monitor.Message(
        "kolesaBB",
        260,
        datetime(2026, 7, 25, 15, 15, tzinfo=UTC),
        "https://betboom.ru/freestream/deko2",
        "https://t.me/kolesaBB/260",
    )

    monitor.notify_activation(
        message,
        str(_entry()["url"]),
        datetime(2026, 7, 25, 15, 45, tzinfo=UTC),
        "api",
        [],
        state,
        action_id=1050,
        verification_status="confirmed",
        server_start_at=datetime(
            2026,
            7,
            25,
            15,
            15,
            3,
            147000,
            tzinfo=UTC,
        ),
    )

    assert order.index("durable_prepare") < order.index("dispatch_wake")
    assert order.index("dispatch_wake") < order.index("telegram_send")
    report = store.day_report("2026-07-25")[0]
    assert report["notifications"][0]["telegram_message_id"] == 99


def test_github_failure_leaves_dispatch_in_retry_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    event_id = store.prepare_event(_entry(), enqueue_notification=False)

    class Response:
        status_code = 403
        text = "rate limit"

    monkeypatch.setattr(
        auto_participation_dispatch,
        "_dispatch_with_recovery",
        lambda *args, **kwargs: (Response(), False, ""),
    )
    summary = auto_participation_dispatch.dispatch_pending(
        store,
        token="token",
        repository="owner/repo",
        branch="main",
    )

    assert summary == {"claimed": 1, "dispatched": 0, "retry": 1}
    db = sqlite3.connect(store.path)
    try:
        pending_auto = db.execute(
            """
            SELECT count(*) FROM outbox
            WHERE kind='auto_participation' AND status='retry'
            """
        ).fetchone()[0]
        row = db.execute(
            "SELECT status, last_error FROM outbox WHERE event_id=? AND kind=?",
            (event_id, "auto_participation"),
        ).fetchone()
    finally:
        db.close()
    assert pending_auto == 1
    assert row is not None and row[0] == "retry"
    assert "http_403" in row[1]


def test_dispatch_ref_never_uses_runtime_commit_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BBVG_DEPLOYMENT_SHA", "9" * 40)
    monkeypatch.setenv("GITHUB_SHA", "8" * 40)
    monkeypatch.setenv("GITHUB_BRANCH", "main")
    assert auto_participation_dispatch.dispatch_ref_from_environment() == "main"

    monkeypatch.setenv("BBVG_AUTO_PARTICIPATION_REF", "production")
    assert (
        auto_participation_dispatch.dispatch_ref_from_environment()
        == "production"
    )


def test_dispatch_can_claim_only_the_newly_notified_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    first = store.prepare_event(_entry(), enqueue_notification=False)
    second_entry = _entry() | {
        "wheel_key": "over",
        "identifier": "over",
        "url": "https://betboom.ru/freestream/over",
        "action_id": 1052,
        "server_start_at": "2026-07-26T11:55:26.955+00:00",
    }
    second = store.prepare_event(second_entry, enqueue_notification=False)

    class Response:
        status_code = 204
        text = ""

    monkeypatch.setattr(
        auto_participation_dispatch,
        "_dispatch_with_recovery",
        lambda *args, **kwargs: (Response(), False, ""),
    )
    summary = auto_participation_dispatch.dispatch_pending(
        store,
        token="token",
        repository="owner/repo",
        branch="main",
        event_ids={second},
    )

    assert summary == {"claimed": 1, "dispatched": 1, "retry": 0}
    db = sqlite3.connect(store.path)
    try:
        statuses = dict(
            db.execute(
                "SELECT event_id, status FROM outbox WHERE kind='auto_participation'"
            ).fetchall()
        )
    finally:
        db.close()
    assert statuses[first] == "pending"
    assert statuses[second] == "completed"


def test_dispatch_wake_without_legacy_marker_keeps_failure_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 26, 11, 56, tzinfo=UTC)
    saved: list[dict[str, object]] = []
    module = SimpleNamespace(
        STATE_PATH=tmp_path / "state.json",
        now_utc=lambda: now,
        parse_datetime=lambda value: None,
        save_state=lambda state: saved.append(json.loads(json.dumps(state))),
    )
    state: dict[str, object] = {
        "active_wheels": {"over": _entry() | {"wheel_key": "over"}},
    }
    assert "auto_participation_event_mode_initialized_at" not in state
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(
        personal_reminder_filter.betboom_auto_participation,
        "_event_token",
        lambda key, entry: "evt:over",
    )
    monkeypatch.setattr(
        personal_reminder_filter.betboom_auto_participation,
        "_eligible_for_event_attempt",
        lambda entry, monitor_module, current: True,
    )
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 60
        return SimpleNamespace(
            returncode=1,
            stdout='{"claimed":1,"dispatched":0,"retry":1}',
            stderr="",
        )

    monkeypatch.setattr(personal_reminder_filter.subprocess, "run", run)

    assert personal_reminder_filter._wake_durable_dispatcher(state, module)
    assert calls == [
        [
            personal_reminder_filter.sys.executable,
            "auto_participation_dispatch.py",
            "--event-id",
            "evt:over",
        ]
    ]
    dispatch = state["auto_participation_dispatch_events"]["evt:over"]
    assert dispatch["status"] == "local_outbox_retry_scheduled"
    assert dispatch["dispatcher_returncode"] == 1
    assert '"retry":1' in dispatch["dispatcher_output"]
    assert len(saved) == 2


def test_runtime_state_publish_retries_cas_and_semantically_merges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_path = tmp_path / "state.json"
    local_path.write_text(
        json.dumps(
            {
                "auto_participation_events": {
                    "evt:over": {
                        "wheel_key": "over",
                        "status": "participated",
                        "bot_success_pending_at": "2026-07-26T12:09:30+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    remote = {
        "monitor_field": "preserve",
        "auto_participation_events": {},
    }

    class Response:
        def __init__(self, status_code: int, payload: dict, text: str = ""):
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self):
            return self._payload

    reads = 0
    writes: list[dict] = []

    def get(*args, **kwargs):
        nonlocal reads
        reads += 1
        return Response(
            200,
            {
                "sha": f"remote-{reads}",
                "content": base64.b64encode(
                    json.dumps(remote).encode("utf-8")
                ).decode("ascii"),
            },
        )

    def put(*args, **kwargs):
        writes.append(kwargs["json"])
        if len(writes) == 1:
            return Response(409, {}, "conflict")
        return Response(200, {"commit": {"sha": "published"}})

    monkeypatch.setattr(auto_participation_bot_sync.requests, "get", get)
    monkeypatch.setattr(auto_participation_bot_sync.requests, "put", put)

    result = auto_participation_bot_sync.publish_runtime_state(
        local_path,
        token="token",
        repository="owner/repo",
    )

    assert result == {
        "branch": "runtime-state",
        "attempt": 2,
        "changed": True,
        "sha": "published",
    }
    assert reads == 2
    merged = json.loads(base64.b64decode(writes[-1]["content"]))
    assert merged["monitor_field"] == "preserve"
    assert merged["auto_participation_events"]["evt:over"]["status"] == "participated"
    assert writes[-1]["branch"] == "runtime-state"
    assert writes[-1]["sha"] == "remote-2"


def test_workflow_payload_materializes_only_the_dispatched_event() -> None:
    state: dict[str, object] = {"active_wheels": {}}
    fake_monitor = SimpleNamespace(
        now_utc=lambda: datetime(2026, 7, 25, 15, 15, 5, tzinfo=UTC)
    )
    payload = {
        "event_id": "evt:0123456789abcdef0123",
        "generation_id": "0123456789abcdef0123",
        "wheel_key": "deko2",
        "identifier": "deko2",
        "url": "https://betboom.ru/freestream/deko2",
        "source": "kolesaBB",
        "source_message_id": 260,
        "source_message_date": "2026-07-25T15:15:00+00:00",
        "source_message_url": "https://t.me/kolesaBB/260",
        "action_id": 1050,
        "server_start_at": "2026-07-25T15:15:03.147+00:00",
        "status": "active",
    }

    event_id = materialize_event_payload(
        state,
        json.dumps(payload),
        received_at=fake_monitor.now_utc(),
    )

    assert event_id == payload["event_id"]
    assert list(state["active_wheels"]) == ["deko2"]
    assert state["active_wheels"]["deko2"]["canonical_event_id"] == event_id


def test_emergency_outcome_is_owner_scoped_and_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = "evt:0123456789abcdef0123"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "auto_participation_events": {
                    event_id: {
                        "event_token": event_id,
                        "account_owner": "vyacheslav",
                        "account_key": "vyacheslav_primary",
                        "account_label": "Аккаунт 1",
                        "status": "already_participating",
                    },
                    f"{event_id}#account:vyacheslav_secondary": {
                        "event_token": event_id,
                        "account_owner": "vyacheslav",
                        "account_key": "vyacheslav_secondary",
                        "account_label": "Аккаунт 2",
                        "status": "participated",
                    },
                    f"{event_id}#account:xflarxx_primary": {
                        "event_token": event_id,
                        "account_owner": "xflarxx",
                        "account_key": "xflarxx_primary",
                        "account_label": "xFLARXx",
                        "status": "participated",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    marker = tmp_path / "sent.json"
    sent: list[dict[str, object]] = []
    monkeypatch.setenv("BOT_CHAT_ID", "123")
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setattr(
        auto_participation_bot_sync.monitor,
        "telegram_api",
        lambda method, payload: (
            sent.append({"method": method, **payload})
            or {"ok": True, "result": {"message_id": 456}}
        ),
    )
    payload = json.dumps(
        {
            "event_id": event_id,
            "identifier": "CTOM23",
            "url": "https://betboom.ru/freestream/CTOM23",
        }
    )

    first = auto_participation_bot_sync.emergency_notify_event(
        state_path,
        payload,
        marker_path=marker,
    )
    second = auto_participation_bot_sync.emergency_notify_event(
        state_path,
        payload,
        marker_path=marker,
    )

    assert first["status"] == "sent"
    assert first["message_id"] == 456
    assert second == {"status": "duplicate_suppressed", "sent": False}
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "123"
    assert "Аккаунт 1" in str(sent[0]["text"])
    assert "Аккаунт 2" in str(sent[0]["text"])
    assert "xFLARXx" not in str(sent[0]["text"])


def test_emergency_outcome_waits_for_every_owner_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = "evt:0123456789abcdef0123"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "auto_participation_events": {
                    event_id: {
                        "event_token": event_id,
                        "account_owner": "vyacheslav",
                        "account_key": "vyacheslav_primary",
                        "status": "participated",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        auto_participation_bot_sync.monitor,
        "telegram_api",
        lambda *_args, **_kwargs: pytest.fail("must not send an incomplete result"),
    )

    result = auto_participation_bot_sync.emergency_notify_event(
        state_path,
        json.dumps({"event_id": event_id, "identifier": "CTOM23"}),
        marker_path=tmp_path / "sent.json",
    )

    assert result["status"] == "account_results_incomplete"
    assert result["sent"] is False


def test_browser_failure_writes_reproducible_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        url = "https://betboom.ru/freestream/deko2"

        @staticmethod
        def screenshot(*, path: str, full_page: bool) -> None:
            assert full_page is True
            Path(path).write_bytes(b"png")

        @staticmethod
        def content() -> str:
            return "<html><button>Об акции</button></html>"

        @staticmethod
        def locator(selector: str):
            raise RuntimeError(selector)

    monkeypatch.setenv("BBVG_BROWSER_ARTIFACT_DIR", str(tmp_path))
    artifact = betboom_participation_browser._save_diagnostics(
        Page(),
        Page.url,
        "button_not_found",
        "only promotion details control is visible",
    )

    target = Path(artifact)
    assert (target / "page.png").read_bytes() == b"png"
    assert "Об акции" in (target / "page.html").read_text(encoding="utf-8")
    metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "button_not_found"
