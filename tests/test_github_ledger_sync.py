from __future__ import annotations

from bbvg.storage.github_sync import merge_event_snapshots


def test_semantic_merge_preserves_success_and_all_append_only_rows() -> None:
    remote = {
        "event_id": "evt:abc",
        "generation_id": "abc",
        "wheel_key": "zonertg16",
        "updated_at": "2026-07-25T09:05:30+00:00",
        "aliases": ["zonertg16#action:701:start"],
        "transitions": [
            {
                "transition_id": "t1",
                "stage": "browser_started",
                "occurred_at": "2026-07-25T09:05:00+00:00",
            }
        ],
        "account_results": [
            {
                "owner_id": "owner-a",
                "account_key": "primary",
                "account_label": "Основной",
                "status": "participated",
                "confirmation": "exact_text_confirmed",
                "finished_at": "2026-07-25T09:05:20+00:00",
            }
        ],
        "account_attempts": [{"attempt_id": "a1", "status": "participated"}],
        "notifications": [
            {
                "delivery_id": "n1",
                "status": "sent",
                "telegram_message_id": 42,
                "sent_at": "2026-07-25T09:05:30+00:00",
            }
        ],
    }
    local = {
        "event_id": "evt:abc",
        "generation_id": "abc",
        "wheel_key": "zonertg16",
        "updated_at": "2026-07-25T09:06:30+00:00",
        "aliases": ["zonertg16#event:abc"],
        "transitions": [
            {
                "transition_id": "t2",
                "stage": "account_result",
                "occurred_at": "2026-07-25T09:06:20+00:00",
            }
        ],
        "account_results": [
            {
                "owner_id": "owner-a",
                "account_key": "primary",
                "account_label": "Основной",
                "status": "button_not_found",
                "confirmation": "dom_scan",
                "finished_at": "2026-07-25T09:06:20+00:00",
            },
            {
                "owner_id": "owner-b",
                "account_key": "secondary",
                "account_label": "Второй",
                "status": "timeout",
                "confirmation": "browser_timeout",
                "finished_at": "2026-07-25T09:06:25+00:00",
            },
        ],
        "account_attempts": [{"attempt_id": "a2", "status": "button_not_found"}],
        "notifications": [
            {"delivery_id": "n1", "status": "failed"},
            {"delivery_id": "n2", "status": "sent", "telegram_message_id": 43},
        ],
    }

    merged = merge_event_snapshots(remote, local)

    results = {
        (row["owner_id"], row["account_key"]): row["status"]
        for row in merged["account_results"]
    }
    assert results == {
        ("owner-a", "primary"): "participated",
        ("owner-b", "secondary"): "timeout",
    }
    assert {row["transition_id"] for row in merged["transitions"]} == {"t1", "t2"}
    assert {row["attempt_id"] for row in merged["account_attempts"]} == {"a1", "a2"}
    first_notification = next(
        row for row in merged["notifications"] if row["delivery_id"] == "n1"
    )
    assert first_notification["status"] == "sent"
    assert first_notification["telegram_message_id"] == 42
