from __future__ import annotations

from datetime import datetime, timezone

import betboom_participation_browser as browser
import telegram_post_links_v2
import wheel_publications_v2


class Candidate:
    def __init__(self, text: str) -> None:
        self.text = text
        self.clicked = False

    def is_visible(self) -> bool:
        return True

    def inner_text(self, timeout: int = 0) -> str:
        return self.text

    def click(self, timeout: int = 0, force: bool = False) -> None:
        self.clicked = True


class Locator:
    def __init__(self, values: list[Candidate]) -> None:
        self.values = values

    def count(self) -> int:
        return len(self.values)

    def nth(self, index: int) -> Candidate:
        return self.values[index]

    def filter(self, **_kwargs):
        return self


class Page:
    def __init__(self, labels: list[str]) -> None:
        self.values = [Candidate(label) for label in labels]
        self.main_frame = object()
        self.frames = [self.main_frame]
        self.url = "https://betboom.ru/freestream/CTOM18"

    def get_by_role(self, *_args, **_kwargs):
        return Locator(self.values)

    def get_by_text(self, *_args, **_kwargs):
        return Locator(self.values)

    def locator(self, *_args, **_kwargs):
        return Locator(self.values)

    def evaluate(self, *_args, **_kwargs):
        return ""

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class TelegramTransport:
    @staticmethod
    def public_source_url(username: str, before: int | None = None) -> str:
        base = f"https://telegram.me/s/{username}"
        return f"{base}?before={before}" if before is not None else base


class Monitor:
    telegram_transport = TelegramTransport()

    @staticmethod
    def now_utc() -> datetime:
        return datetime(2026, 7, 24, 12, 11, 30, tzinfo=timezone.utc)


def test_preparation_clicks_cookie_but_never_promo_state() -> None:
    page = Page(["Окей", "Об акции", "Другие акции", "Участвовать"])

    actions = browser._prepare_page(page, 1000)

    assert page.values[0].clicked is True
    assert page.values[1].clicked is False
    assert page.values[2].clicked is False
    assert actions == ["main:Окей"]


def test_participation_button_is_found_after_preparation() -> None:
    page = Page(["Окей", "Об акции", "Участвовать"])
    browser._prepare_page(page, 1000)

    clicked, location = browser._click_candidates(page, 1000)

    assert clicked is True
    assert location == "main:Участвовать"
    assert page.values[2].clicked is True


def test_preliminary_alert_blocks_second_source_activation_alert() -> None:
    assert wheel_publications_v2._any_notification_suppressed(
        lambda _state, _link: True,
        lambda _state, _link: False,
        {"url_alerts": {"ctom18": {"alerted_at": "2026-07-24T12:22:47+00:00"}}},
        "https://betboom.ru/freestream/CTOM18",
        "ctom18",
    ) is True


def test_fresh_telegram_url_preserves_before_and_changes_cache_key() -> None:
    latest = telegram_post_links_v2.fresh_public_source_url(Monitor, "kolesaBB")
    history = telegram_post_links_v2.fresh_public_source_url(
        Monitor,
        "kolesaBB",
        before=237,
    )

    assert latest.endswith("?bbvg_fresh=59496503")
    assert history.endswith("?before=237&bbvg_fresh=59496503")
