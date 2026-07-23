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
