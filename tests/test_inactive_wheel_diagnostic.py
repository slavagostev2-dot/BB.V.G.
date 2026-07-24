from types import SimpleNamespace

import admin_panel_v2


def test_diagnostic_shows_inactive_betboom_event(monkeypatch) -> None:
    panel = object.__new__(admin_panel_v2.TelegramPanelV2)
    panel.snapshot = lambda: SimpleNamespace(
        fast=["kolesaBB"],
        nightly=[],
        state={
            "pending_posts": {},
            "active_wheels": {},
            "wheel_generation_observations": {
                "current": {
                    "wheel_key": "aunkere",
                    "action_id": 1021,
                    "server_start_at": "2026-07-24T17:55:53.181000+00:00",
                    "last_seen_at": "2026-07-24T18:33:21.656342+00:00",
                    "statuses": {"inactive": 1},
                }
            },
        },
    )
    panel.load_access = lambda: {
        "settings": {"monitor_interval_minutes": 1}
    }

    response = SimpleNamespace(
        status_code=200,
        text=(
            '<div class="tgme_widget_message" data-post="kolesaBB/245">'
            '<div class="tgme_widget_message_text">Колесо BetBoom</div>'
            '<a href="https://betboom.ru/freestream/aunkere">Открыть</a>'
            '</div>'
        ),
    )
    monkeypatch.setattr(admin_panel_v2.requests, "get", lambda *args, **kwargs: response)

    result = panel.diagnose_input("https://t.me/kolesaBB/245")

    assert "основная проверка каждую минуту" in result
    assert "колесо найдено монитором" in result
    assert "уже закрыл участие" in result
    assert "action_id 1021" in result
    assert "пост ещё не попал" not in result
