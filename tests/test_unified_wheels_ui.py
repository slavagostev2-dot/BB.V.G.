from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

from bbvg.bot import wheel_screen
import notification_button_recovery


class FakeRuntime:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.snap = SimpleNamespace(state={})

    def snapshot(self, force: bool = False):
        assert force is True
        return self.snap

    def _collect_current_wheels(self):
        return list(self.items)

    def _monitor_status(self):
        return {"last_successful_iteration_at": "2026-09-06T12:00:00+00:00"}

    @staticmethod
    def fmt_dt(value):
        return "06.09.2026 19:00"

    @staticmethod
    def age_text(value):
        return "1 мин. назад"

    @staticmethod
    def parse_dt(value):
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed

    @staticmethod
    def remaining(value):
        return "42 мин."

    @staticmethod
    def _sources_for_item(snap, key, item):
        return list(item.get("sources") or [item.get("source") or "source"])

    @staticmethod
    def with_nav(rows=None):
        return {"inline_keyboard": rows or []}

    def send(self, text, *, reply_markup=None, chat_id=None):
        self.sent.append((text, reply_markup or {}))
        return {"ok": True}


def _callbacks(markup: dict[str, Any]) -> list[str]:
    return [
        str(button.get("callback_data") or "")
        for row in markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict) and button.get("callback_data")
    ]


def test_unified_wheels_screen_keeps_three_current_states_and_sources() -> None:
    runtime = FakeRuntime(
        [
            {
                "_key": "pending",
                "identifier": "pending",
                "available_at": "2099-01-01T12:00:00+00:00",
                "url": "https://betboom.ru/freestream/pending",
                "sources": ["one", "two"],
            },
            {
                "_key": "timed",
                "identifier": "timed",
                "deadline": "2099-01-01T13:00:00+00:00",
                "url": "https://betboom.ru/freestream/timed",
                "source": "three",
            },
            {
                "_key": "unknown",
                "identifier": "unknown",
                "url": "https://betboom.ru/freestream/unknown",
                "source": "four",
            },
        ]
    )

    wheel_screen.render(runtime)

    text, markup = runtime.sent[-1]
    assert "🎡 <b>Колёса — 3</b>" in text
    assert "🟠 Ожидает запуска" in text
    assert "🟢 Время прокрутки известно" in text
    assert "🟡 Время уточняется" in text
    assert "@one, @two" in text
    assert "@three" in text
    assert "@four" in text
    rendered = str(markup)
    assert "wheel:time:" not in rendered
    assert "wheel:inactive:" not in rendered
    assert "wheel:finished:" not in rendered
    assert "wheel:part:" not in rendered


def test_unified_wheels_screen_uses_fifteen_item_pagination() -> None:
    items = [
        {
            "_key": f"wheel-{index}",
            "identifier": f"wheel-{index}",
            "deadline": "2099-01-01T13:00:00+00:00",
            "url": f"https://betboom.ru/freestream/wheel-{index}",
            "source": "source",
        }
        for index in range(1, 17)
    ]
    runtime = FakeRuntime(items)

    wheel_screen.render(runtime, 0)
    first_text, first_markup = runtime.sent[-1]
    assert "Страница: <b>1 из 2</b>" in first_text
    assert "<b>15. <code>wheel-15</code></b>" in first_text
    assert "wheel-16" not in first_text
    assert "page:active:1" in _callbacks(first_markup)

    wheel_screen.render(runtime, 1)
    second_text, second_markup = runtime.sent[-1]
    assert "Страница: <b>2 из 2</b>" in second_text
    assert "<b>16. <code>wheel-16</code></b>" in second_text
    assert "page:active:0" in _callbacks(second_markup)
    assert "refresh:active:1" in _callbacks(second_markup)


def test_empty_unified_wheels_screen_keeps_freshness_and_refresh() -> None:
    runtime = FakeRuntime([])

    wheel_screen.render(runtime)

    text, markup = runtime.sent[-1]
    assert "Колёс сейчас нет" in text
    assert "Обновлено: 06.09.2026 19:00 (1 мин. назад)" in text
    assert _callbacks(markup) == ["refresh:active:0"]


def test_live_control_center_entrypoint_delegates_to_single_renderer(monkeypatch) -> None:
    called: list[tuple[object, int]] = []
    monkeypatch.setattr(
        notification_button_recovery.wheel_screen,
        "render",
        lambda panel, page=0: called.append((panel, page)),
    )
    panel = notification_button_recovery.TelegramPanelRuntimeButtonRecovery.__new__(
        notification_button_recovery.TelegramPanelRuntimeButtonRecovery
    )

    panel.show_active(3)

    assert called == [(panel, 3)]
