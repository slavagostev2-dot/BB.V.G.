from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import monitor
import monitor_data as data_store


ROOT = Path(__file__).resolve().parent


def fake_page(text: str):
    class FakeResponse:
        status_code = 200

        def __init__(self, value: str) -> None:
            self.text = value

        def raise_for_status(self) -> None:
            return None

    return FakeResponse(text)


def main() -> None:
    assert monitor.normalize_url(
        "http://www.betboom.ru/freestream/Shoke/?x=1"
    ) == "https://betboom.ru/freestream/Shoke"
    assert monitor.wheel_key(
        "https://betboom.ru/freestream/Shoke"
    ) == monitor.wheel_key("https://www.betboom.ru/freestream/shoke/?from=tg")

    published = datetime(2026, 7, 13, 12, 0, tzinfo=monitor.UTC)
    deadline, _ = monitor.infer_deadline("Крутим через 1 час 20 минут", published)
    assert deadline == published + timedelta(hours=1, minutes=20)

    deadline = monitor.countdown_deadline("До прокрутки 00:15:30", published)
    assert deadline == published + timedelta(minutes=15, seconds=30)

    original_request = monitor.request_with_retries
    try:
        monitor.request_with_retries = lambda *args, **kwargs: fake_page(
            "<html><body>Пока ждёшь следующий запуск, заглядывай в другие акции</body></html>"
        )
        inspection = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/old-wheel"
        )
        assert inspection.status == "inactive"

        monitor.request_with_retries = lambda *args, **kwargs: fake_page(
            '<html><body><button aria-label="Участвовать">Участвовать</button></body></html>'
        )
        inspection = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/live-wheel"
        )
        assert inspection.status == "active"
        assert "кнопка" in inspection.method
        assert "Участвовать" in inspection.page_excerpt
    finally:
        monitor.request_with_retries = original_request

    message = monitor.Message(
        source="test",
        message_id=77,
        date=monitor.now_utc(),
        text="https://betboom.ru/freestream/pending-wheel",
        message_url="https://t.me/test/77",
    )
    link = "https://betboom.ru/freestream/pending-wheel"
    key = monitor.notification_key(message, link)
    state = {
        "pending_posts": {},
        "activation_alerts": {},
        "url_alerts": {},
        "button_contexts": {},
        "manual_overrides": {},
        "seen": {},
        "active_wheels": {},
        "participating_wheels": {},
    }
    monitor.remember_pending(
        state,
        key,
        message,
        link,
        "inactive",
        "not active yet",
        initial_notified=True,
    )
    assert key in state["pending_posts"]
    assert monitor.pending_initial_notified(state["pending_posts"][key])
    restored = monitor.pending_message(state["pending_posts"][key])
    assert restored is not None and restored.message_id == 77

    original_inspection = monitor.inspect_wheel_page
    monitor.inspect_wheel_page = lambda value: monitor.WheelInspection(
        "active", None, "активная кнопка: найдено «участвовать»", "Участвовать"
    )
    try:
        assessment = monitor.assess_pending_wheel(message, link, state)
        assert assessment.should_notify and assessment.status == "active"
        assert assessment.deadline is None and "кнопка" in assessment.method
    finally:
        monitor.inspect_wheel_page = original_inspection

    assert not monitor.is_activation_suppressed(state, link)
    monitor.remember_activation(state, link, None)
    assert monitor.is_activation_suppressed(state, link)


    markup_state = {"button_contexts": {}}
    markup = monitor.wheel_reply_markup(
        markup_state,
        message,
        link,
        active=False,
        status="unconfirmed",
        method="таймер не найден",
        page_excerpt="пример страницы",
    )
    labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert "🎡 Открыть колесо" in labels
    assert "✅ Участвую" in labels
    assert "📋 Активные колёса" in labels
    assert "🔄 Проверить" in labels
    assert "✅ Активно" in labels
    assert "🚫 Неактивно" in labels
    assert "🕒 Нет времени" in labels
    assert markup_state["button_contexts"]

    context = next(iter(markup_state["button_contexts"].values()))
    markup_state.setdefault("active_wheels", {})
    markup_state.setdefault("participating_wheels", {})
    monitor.remember_active_wheel(
        markup_state,
        message,
        link,
        published + timedelta(hours=2),
        "active",
        "test",
    )
    assert "pending-wheel" in markup_state["active_wheels"]
    monitor.mark_participating(markup_state, context)
    assert monitor.is_participating(markup_state, link)
    assert "✅ участвую" in monitor.active_wheels_text(markup_state)

    known_entry = {
        "first_notified_at": published.isoformat(),
        "deadline": (published + timedelta(hours=3)).isoformat(),
    }
    assert not monitor.known_reminder_due(known_entry, published + timedelta(hours=1))
    assert monitor.known_reminder_due(known_entry, published + timedelta(hours=2))
    known_entry["known_reminder_sent_at"] = (published + timedelta(hours=2)).isoformat()
    assert not monitor.known_reminder_due(known_entry, published + timedelta(hours=2, minutes=5))

    unknown_entry = {"first_notified_at": published.isoformat()}
    assert not monitor.unknown_reminder_due(unknown_entry, published + timedelta(minutes=29))
    assert monitor.unknown_reminder_due(unknown_entry, published + timedelta(minutes=30))

    unknown = {"version": 1, "samples": []}
    added = data_store.record_unknown_timer_sample(
        unknown,
        source="test",
        message_id=77,
        message_url="https://t.me/test/77",
        wheel_url=link,
        wheel_identifier="pending-wheel",
        status="unknown",
        method="таймер не найден",
        telegram_text="Новый формат времени",
        page_excerpt="Необычная строка таймера",
    )
    assert added and len(unknown["samples"]) == 1
    assert not data_store.record_unknown_timer_sample(
        unknown,
        source="test",
        message_id=77,
        message_url="https://t.me/test/77",
        wheel_url=link,
        wheel_identifier="pending-wheel",
        status="unknown",
        method="таймер не найден",
        telegram_text="Новый формат времени",
        page_excerpt="Необычная строка таймера",
    )

    catalog = data_store.load_partner_catalog()
    channels = data_store.flatten_partner_channels(catalog)
    assert channels["shadowkekw"]["relationship"] == "betboom_partner"
    assert channels["aunkeretg"]["scan_mode"] == "fast"
    assert channels["dekocsoff"]["scan_mode"] == "fast"
    assert channels["ct0mislove"]["scan_mode"] == "fast"
    assert channels["blindzonexgod"]["scan_mode"] == "fast"
    assert channels["daynezz"]["scan_mode"] == "fast"
    assert channels["betboomteamcs2"]["scan_mode"] == "nightly"
    assert channels["narodcast"]["relationship"] == "betboom_partner"
    assert channels["narodcast"]["channel_type"] == "main"
    assert "frixa_betboom" not in channels
    assert "gazazor" not in channels

    quick = {
        item.casefold()
        for item in data_store.operational_sources(
            monitor.read_list(ROOT / "public_sources.txt"), "fast"
        )
    }
    nightly = {
        item.casefold()
        for item in data_store.operational_sources(
            monitor.read_list(ROOT / "source_catalog.txt"), "nightly"
        )
    }
    assert not quick.intersection(nightly), "Быстрый и ночной списки пересекаются"
    assert "kolesabb" in quick
    assert "homakolesa" in quick
    assert "narodcast" in quick
    assert "frixa_betboom" not in quick
    assert "gazazor" not in quick
    assert "dartwager" in quick
    assert "amam0610" in quick
    assert "aunkeretg" in quick
    assert "dekocsoff" in quick
    assert "ct0mislove" in quick
    assert "blindzonexgod" in quick
    assert "daynezz" in quick
    assert monitor.NEW_SOURCE_CATCHUP_MINUTES >= 0

    health = {"version": 1, "sources": {}}
    for _ in range(data_store.QUARANTINE_FAILURE_THRESHOLD):
        quarantined = data_store.record_source_problem(
            health, "broken_channel", "error", "test"
        )
    assert quarantined
    assert not data_store.source_due_for_check(health, "broken_channel")
    data_store.record_source_success(health, "broken_channel", 10)
    assert data_store.source_due_for_check(health, "broken_channel")

    stats = {"version": 1, "sources": {}, "daily": {}}
    assert data_store.mark_unique_wheel_post(stats, "test", key, "pending-wheel")
    assert not data_store.mark_unique_wheel_post(stats, "test", key, "pending-wheel")


    timezone_stats = {"version": 1, "sources": {}, "daily": {}}
    data_store.increment_stat(
        timezone_stats,
        "test",
        "checks",
        at=datetime(2026, 7, 12, 18, 0, tzinfo=monitor.UTC),
    )
    assert "2026-07-13" in timezone_stats["daily"]

    project_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "monitor.py",
            ROOT / "nightly_discovery.py",
            ROOT / ".github/workflows/monitor.yml",
        )
    )
    assert "known_freestream_ids" not in project_text
    assert "check_known_links" not in project_text

    print("Self-test passed.")


if __name__ == "__main__":
    main()
