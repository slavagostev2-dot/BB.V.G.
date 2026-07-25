from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import auto_participation_dispatch
import betboom_participation_browser
import monitor
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
