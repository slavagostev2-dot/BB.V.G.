from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import telegram_wheel_metadata_hotfix as hotfix


def test_split_telegram_text_blocks_keep_timer():
    hotfix.self_test()


def test_recent_seen_wheel_can_recover_metadata_without_notification():
    now = datetime(2026, 8, 26, 16, 20, tzinfo=timezone.utc)
    published = datetime(2026, 8, 26, 14, 42, 24, tzinfo=timezone.utc)
    state = {
        "active_wheels": {
            "zonertg13": {
                "source": "mechanogun",
                "message_id": 36154,
                "message_date": published.isoformat(),
                "message_url": "https://telegram.me/mechanogun/36154",
                "message_text": "ЗАЛЕТАЙ НА КОЛЕСО ФРИБЕТОВ",
                "deadline": None,
                "needs_manual_time": True,
            }
        },
        "participating_wheels": {"zonertg13": {}},
        "button_contexts": {
            "ctx": {
                "wheel_key": "zonertg13",
                "url": "https://betboom.ru/freestream/zonertg13",
            }
        },
    }

    class FakeMonitor:
        UTC = timezone.utc
        saved = None

        @staticmethod
        def now_utc():
            return now

        @staticmethod
        def parse_datetime(value):
            if isinstance(value, datetime):
                return value
            if not isinstance(value, str) or not value:
                return None
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        @staticmethod
        def load_state():
            return deepcopy(state)

        @staticmethod
        def save_state(value):
            FakeMonitor.saved = deepcopy(value)

        @staticmethod
        def extract_links(text):
            marker = "https://betboom.ru/freestream/zonertg13"
            return [marker] if marker in text else []

        @staticmethod
        def wheel_key(link):
            return str(link).split("/freestream/", 1)[-1].split("?", 1)[0].casefold()

        @staticmethod
        def infer_deadline(text, published_at):
            if "ИТОГИ ЧЕРЕЗ 10 ЧАСОВ" in text:
                return published_at + timedelta(hours=10), "текст Telegram: относительное время"
            return None, ""

        @staticmethod
        def participation_expiry(deadline, *, current=None):
            return deadline + timedelta(minutes=30)

    direct = SimpleNamespace(
        source="mechanogun",
        message_id=36154,
        date=published,
        message_url="https://telegram.me/mechanogun/36154",
        text=(
            "ЗАЛЕТАЙ НА КОЛЕСО ФРИБЕТОВ\n"
            "10 ПО 1000 20 ПО 500\n"
            "ИТОГИ ЧЕРЕЗ 10 ЧАСОВ\n"
            "https://betboom.ru/freestream/zonertg13"
        ),
    )
    summary = hotfix.recover_recent_untimed_wheels(
        FakeMonitor,
        direct_fetcher=lambda source, message_id: direct,
    )

    assert summary == {"checked": 1, "text_refreshed": 1, "deadline_recovered": 1}
    assert FakeMonitor.saved is not None
    entry = FakeMonitor.saved["active_wheels"]["zonertg13"]
    assert entry["deadline"] == "2026-08-27T00:42:24+00:00"
    assert entry["needs_manual_time"] is False
    assert entry["deadline_source"] == "telegram_direct_post"
    assert "ИТОГИ ЧЕРЕЗ 10 ЧАСОВ" in entry["message_text"]
    assert FakeMonitor.saved["participating_wheels"]["zonertg13"]["deadline"] == entry["deadline"]
    assert FakeMonitor.saved["button_contexts"]["ctx"]["deadline"] == entry["deadline"]
