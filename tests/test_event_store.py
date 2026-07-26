from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bbvg.storage import (
    EventStore,
    canonical_event_id,
    event_id_from_entry,
    status_confidence,
)
from bbvg.storage.event_store import SUCCESS_STATUSES


def _store(tmp_path: Path) -> EventStore:
    return EventStore(
        tmp_path / "events.sqlite3",
        audit_path=tmp_path / "events.jsonl",
        notification_audit_path=tmp_path / "notifications.jsonl",
    )


def _event(
    *,
    action_id: int = 1048,
    server_start_at: str = "2026-07-25T13:46:14.845+00:00",
) -> dict[str, object]:
    return {
        "wheel_key": "ctom22",
        "url": "https://betboom.ru/wheel/ctom22",
        "action_id": action_id,
        "server_start_at": server_start_at,
        "source": "kolesabb",
        "message_id": 251,
        "message_date": "2026-07-25T13:46:00+00:00",
        "message_url": "https://t.me/kolesaBB/251",
        "verification_status": "confirmed",
        "status": "active",
    }


def _rows(path: Path, query: str) -> list[sqlite3.Row]:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(query).fetchall()
    finally:
        db.close()


def test_same_url_creates_distinct_generations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.prepare_event(_event())
    second = store.prepare_event(
        _event(
            action_id=1049,
            server_start_at="2026-07-25T14:46:14.845+00:00",
        )
    )

    assert first != second
    assert len(store.day_report("2026-07-25")) == 2


def test_prepare_is_atomic_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event_id = store.prepare_event(_event())
    assert store.prepare_event(_event()) == event_id

    events = _rows(store.path, "SELECT * FROM events")
    outbox = _rows(store.path, "SELECT * FROM outbox")
    transitions = _rows(store.path, "SELECT * FROM event_transitions")
    assert len(events) == 1
    assert {row["kind"] for row in outbox} == {
        "auto_participation",
        "github_ledger_sync",
        "new_wheel_notification",
    }
    assert {row["stage"] for row in transitions} >= {
        "source_discovered",
        "api_confirmed",
        "persisted",
        "dispatch_queued",
    }


def test_concurrent_prepare_does_not_lose_or_duplicate_dispatch(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        event_ids = list(pool.map(lambda _: store.prepare_event(_event()), range(40)))

    assert len(set(event_ids)) == 1
    assert len(_rows(store.path, "SELECT * FROM events")) == 1
    assert len(
        _rows(
            store.path,
            "SELECT * FROM outbox WHERE kind='auto_participation'",
        )
    ) == 1


def test_provisional_identity_is_promoted_without_duplicate_event(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    provisional_entry = _event()
    provisional_entry.pop("action_id")
    provisional_entry.pop("server_start_at")
    provisional = store.prepare_event(provisional_entry)
    canonical = store.prepare_event(_event())

    assert provisional.startswith("pending:")
    assert canonical == canonical_event_id(
        "ctom22",
        1048,
        "2026-07-25T13:46:14.845+00:00",
    )
    assert store.resolve_event_id(provisional) == canonical
    assert len(_rows(store.path, "SELECT * FROM events")) == 1


def test_promoted_provisional_id_can_be_reused_by_later_unknown_generation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    provisional_entry = _event()
    provisional_entry.pop("action_id")
    provisional_entry.pop("server_start_at")
    provisional = store.prepare_event(provisional_entry)
    canonical = store.prepare_event(_event())

    later_unknown = dict(provisional_entry)
    later_unknown["action_id"] = 1049
    reused = store.prepare_event(
        later_unknown,
        enqueue_participation=False,
        enqueue_notification=False,
    )

    assert store.resolve_event_id(provisional) == canonical
    assert reused != provisional
    assert reused.startswith("pending:")
    assert len(_rows(store.path, "SELECT * FROM events")) == 2


def test_confirmed_success_is_monotonic_per_account(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event_id = store.prepare_event(_event())
    accepted = store.record_account_result(
        event_id,
        owner_id="owner-a",
        account_key="primary",
        account_label="Основной",
        status="participated",
        confirmation="exact_button_and_api_confirmed",
        attempt_count=1,
    )
    rejected = store.record_account_result(
        event_id,
        owner_id="owner-a",
        account_key="primary",
        account_label="Основной",
        status="button_not_found",
        confirmation="dom_scan",
        error_text="late retry",
        attempt_count=2,
    )

    result = store.day_report("2026-07-25")[0]["account_results"][0]
    assert accepted is True
    assert rejected is False
    assert result["status"] == "participated"
    assert result["attempt_count"] == 1
    assert len(_rows(store.path, "SELECT * FROM account_attempts")) == 2


def test_telegram_only_mark_is_not_a_confirmed_account_success() -> None:
    assert "already_marked_in_bot" not in SUCCESS_STATUSES
    assert "already_participating" in SUCCESS_STATUSES
    assert status_confidence("already_marked_in_bot", "") < status_confidence(
        "participated",
        "betboom_post_click",
    )


def test_accounts_and_notification_audit_are_isolated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event_id = store.prepare_event(_event())
    for owner, account, label in (
        ("owner-a", "primary", "Основной"),
        ("owner-a", "reserve", "Резервный"),
        ("owner-b", "primary", "Другой владелец"),
    ):
        store.record_account_result(
            event_id,
            owner_id=owner,
            account_key=account,
            account_label=label,
            status="participated",
            confirmation="confirmed_text",
        )
    store.record_notification(
        event_id,
        notification_type="new_wheel",
        recipient_scope="owner-scope-a",
        status="sent",
        telegram_message_id=123,
        sent_at="2026-07-25T13:47:00+00:00",
    )

    report = store.day_report("2026-07-25")[0]
    assert {
        (row["owner_id"], row["account_key"], row["account_label"])
        for row in report["account_results"]
    } == {
        ("owner-a", "primary", "Основной"),
        ("owner-a", "reserve", "Резервный"),
        ("owner-b", "primary", "Другой владелец"),
    }
    assert report["notifications"][0]["telegram_message_id"] == 123
    assert event_id_from_entry(_event()) == event_id
    audit = (tmp_path / "notifications.jsonl").read_text(encoding="utf-8")
    assert '"recipient_scope":"owner-scope-a"' in audit
    assert "telegram" not in audit.casefold() or "telegram_message_id" in audit
