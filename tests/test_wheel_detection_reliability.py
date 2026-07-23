from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import auto_participation_owner_sync
import notification_integrity_v2
import wheel_detection_reliability as reliability

UTC = timezone.utc


@dataclass
class Message:
    source: str
    message_id: int
    date: datetime
    text: str = ""


def test_creator_overlap_recovers_older_author_post_without_fetching_collector():
    reliability._BACKFILL_LAST_AT.clear()
    current = [
        Message(
            "mechanogun",
            value,
            datetime(2026, 7, 23, 10, value % 60, tzinfo=UTC),
        )
        for value in range(101, 116)
    ]
    collector = [
        Message(
            "amam0610",
            value,
            datetime(2026, 7, 23, 10, value % 60, tzinfo=UTC),
        )
        for value in range(201, 216)
    ]
    older = Message(
        "mechanogun",
        100,
        datetime(2026, 7, 23, 9, 15, tzinfo=UTC),
        "https://betboom.ru/freestream/zonertg14",
    )
    calls: list[tuple[str, int | None]] = []

    def base(_sources):
        return {
            "mechanogun": list(current),
            "amam0610": list(collector),
        }, {}, []

    def fetch_public(source: str, before: int | None = None):
        calls.append((source, before))
        return [older] if source == "mechanogun" else []

    monitor = SimpleNamespace(fetch_public_channel=fetch_public)
    entry = SimpleNamespace(_original_fetch_all_sources=base)
    reliability.install_creator_overlap(
        monitor,
        entry,
        creator_sources={"mechanogun", "amam0610"},
        collector_sources={"amam0610"},
        monotonic=lambda: 100.0,
    )

    results, errors, empty = entry._original_fetch_all_sources(
        ["mechanogun", "amam0610"]
    )

    assert not errors
    assert not empty
    assert calls == [("mechanogun", 101)]
    assert results["mechanogun"][0].message_id == 100
    assert len(results["mechanogun"]) == 16
    assert len(results["amam0610"]) == 15


def test_notification_title_does_not_claim_late_duplicate_is_new():
    text = reliability.clarify_notification_text(
        "🎡 <b>Новое колесо BetBoom</b>"
    )
    assert text == "🎡 <b>Обнаружено колесо BetBoom</b>"


def test_automatic_participation_has_explicit_button_label():
    monitor = SimpleNamespace(
        wheel_key=lambda value: value.rsplit("/", 1)[-1].casefold()
    )
    state = {
        "participating_wheels": {
            "zonertg14": {
                "participation_source": "betboom_browser_recovery",
            }
        },
        "active_wheels": {},
    }
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Участие отмечено",
                    "callback_data": "bb:n:token",
                }
            ]
        ]
    }

    result = reliability.clarify_participation_markup(
        monitor,
        state,
        "https://betboom.ru/freestream/zonertg14",
        markup,
    )

    assert (
        result["inline_keyboard"][0][0]["text"]
        == reliability.AUTO_PARTICIPATION_BUTTON_TEXT
    )


def _parse_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _duplicate_state():
    return {
        "active_wheels": {
            "zonertg14": {
                "recovered_initial_notification_pending_at": "2026-07-23T11:59:16+00:00",
                "recovered_initial_notification_reason": "recovery_discovered_missing_event",
                "first_notified_at": "2026-07-23T13:22:09+00:00",
                "last_notification_at": "2026-07-23T14:28:42+00:00",
            }
        }
    }


def test_recovered_notification_pending_is_cleared_without_second_send():
    sends: list[str] = []
    monitor = SimpleNamespace(
        parse_datetime=_parse_datetime,
        now_utc=lambda: datetime(2026, 7, 23, 14, 29, tzinfo=UTC),
    )

    def original(state):
        pending = any(
            isinstance(entry, dict)
            and entry.get("recovered_initial_notification_pending_at")
            for entry in state.get("active_wheels", {}).values()
        )
        if pending:
            sends.append("sent")
        return {"sent": int(pending), "failed": 0, "changed": bool(pending)}

    runtime = SimpleNamespace(
        _deliver_recovered_initial_notifications=original,
    )
    reliability.install_recovered_notification_guard(monitor, runtime)
    state = _duplicate_state()

    result = runtime._deliver_recovered_initial_notifications(state)
    entry = state["active_wheels"]["zonertg14"]

    assert sends == []
    assert result["sent"] == 0
    assert result["skipped_already_delivered"] == 1
    assert result["changed"] is True
    assert "recovered_initial_notification_pending_at" not in entry
    assert entry["recovered_initial_notification_sent_at"] == (
        "2026-07-23T14:28:42+00:00"
    )


def test_final_process_guard_runs_before_composed_lifecycle_sender():
    sends: list[str] = []

    def process_active(state, _stats):
        if state["active_wheels"]["zonertg14"].get(
            "recovered_initial_notification_pending_at"
        ):
            sends.append("sent")
        return {"changed": False}

    monitor = SimpleNamespace(
        parse_datetime=_parse_datetime,
        now_utc=lambda: datetime(2026, 7, 23, 14, 30, tzinfo=UTC),
        process_active_wheels=process_active,
    )
    reliability.install_final_process_guard(monitor)
    state = _duplicate_state()

    result = monitor.process_active_wheels(state, {})
    entry = state["active_wheels"]["zonertg14"]

    assert sends == []
    assert result["recovered_duplicates_suppressed"] == 1
    assert result["changed"] is True
    assert "recovered_initial_notification_pending_at" not in entry
    assert monitor.process_active_wheels.__module__ == "wheel_lifecycle_v2"


def test_control_center_edits_sent_card_with_auto_label(monkeypatch):
    monkeypatch.delattr(
        auto_participation_owner_sync,
        "_bbvg_auto_button_clarity_installed",
        raising=False,
    )
    monkeypatch.setattr(
        notification_integrity_v2,
        "participation_message_id",
        lambda _chat_id, _token: 77,
    )
    reliability.install_owner_notification_update()

    class Panel:
        def __init__(self):
            self.payload = None

        def telegram_api(self, method, payload):
            self.payload = (method, payload)
            return {"ok": True}

    panel = Panel()
    assert auto_participation_owner_sync._mark_original_notification(
        panel,
        "1",
        {
            "button_token": "abc",
            "url": "https://betboom.ru/freestream/zonertg14",
        },
    )
    button = panel.payload[1]["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == reliability.AUTO_PARTICIPATION_BUTTON_TEXT
