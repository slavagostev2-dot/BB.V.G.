from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import monitor
import monitor_data as data_store


ROOT = Path(__file__).resolve().parent


def fake_api(payload: dict, status_code: int = 200):
    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self) -> dict:
            return payload

        def raise_for_status(self) -> None:
            return None

    return FakeResponse()


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

    original_request = monitor.request_with_retries
    try:
        monitor.request_with_retries = lambda *args, **kwargs: fake_api(
            {
                "code": 200,
                "status": "OK",
                "info": {
                    "action_id": 866,
                    "start_dttm": "2026-07-16T14:26:28.209Z",
                    "duration_min": 15,
                    "is_ended": True,
                    "is_early": False,
                },
            }
        )
        inspection = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/old-wheel"
        )
        assert inspection.status == "inactive"
        assert inspection.action_id == 866

        started = monitor.now_utc() - timedelta(minutes=5)
        monitor.request_with_retries = lambda *args, **kwargs: fake_api(
            {
                "code": 200,
                "status": "OK",
                "info": {
                    "action_id": 692,
                    "start_dttm": started.isoformat(),
                    "duration_min": 600,
                    "is_ended": False,
                    "is_early": False,
                },
            }
        )
        inspection = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/live-wheel"
        )
        assert inspection.status == "active"
        assert inspection.action_id == 692
        assert inspection.deadline == started + timedelta(minutes=600)
        assert inspection.verification_status == monitor.WHEEL_VERIFICATION_CONFIRMED
    finally:
        monitor.request_with_retries = original_request

    message = monitor.Message(
        source="test",
        message_id=77,
        date=monitor.now_utc(),
        text="https://betboom.ru/freestream/pending-wheel",
        message_url="https://telegram.me/test/77",
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
    assert "📨 Пост" in labels
    for removed_label in ("🔄 Проверить", "✅ Активно", "🚫 Неактивно", "🕒 Нет времени"):
        assert removed_label not in labels
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
        message_url="https://telegram.me/test/77",
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
        message_url="https://telegram.me/test/77",
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
    assert channels["betboomteamcs2"]["scan_mode"] == "fast"
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
    approved = quick | nightly
    assert len(approved) >= 66, "В основном и ночном режимах должны оставаться как минимум 66 утверждённых источников"
    assert not quick.intersection(nightly), "Быстрый и ночной списки пересекаются"
    assert "kolesabb" in approved
    assert "homakolesa" in approved
    assert "narodcast" in approved
    assert "frixa_betboom" not in approved
    assert "gazazor" not in approved
    assert "dartwager" in approved
    assert "amam0610" in approved
    assert "aunkeretg" in approved
    assert "dekocsoff" in approved
    assert "ct0mislove" in approved
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

    inactivity_now = datetime(2026, 7, 20, 12, 0, tzinfo=monitor.UTC)
    inactivity_stats = {"version": 1, "sources": {}, "daily": {}}
    data_store.record_source_check_stats(
        inactivity_stats,
        "quiet_channel",
        "ok",
        at=inactivity_now - timedelta(days=8),
    )
    rows = data_store.sources_without_recent_wheels(
        inactivity_stats, ["quiet_channel"], minimum_days=7, at=inactivity_now
    )
    assert rows and rows[0][0] == "quiet_channel"
    data_store.set_stat_timestamp(
        inactivity_stats,
        "quiet_channel",
        "last_wheel_post_at",
        inactivity_now - timedelta(days=2),
    )
    assert not data_store.sources_without_recent_wheels(
        inactivity_stats, ["quiet_channel"], minimum_days=7, at=inactivity_now
    )

    report_text = monitor.source_inactivity_report_text(rows)
    assert "quiet_channel" in report_text
    assert "ночной режим" in report_text

    timezone_stats = {"version": 1, "sources": {}, "daily": {}}
    data_store.increment_stat(
        timezone_stats,
        "test",
        "checks",
        at=datetime(2026, 7, 12, 18, 0, tzinfo=monitor.UTC),
    )
    assert "2026-07-13" in timezone_stats["daily"]

    legacy_ratings = {
        "version": 1,
        "sources": {"test": {"wheel_posts": 3, "quality_score": 40, "last_wheel_post_at": inactivity_now.isoformat()}},
        "daily": {"2026-07-13": {"sources": {"test": {"wheel_posts": 3}}, "totals": {"wheel_posts": 3}}},
        "admin_wheel_decisions": {"old-wheel": {"decision": "confirmed"}},
    }
    assert data_store.apply_source_rating_epoch(legacy_ratings)
    assert legacy_ratings["source_rating_epoch_day"] == "2026-07-17"
    assert "wheel_posts" not in legacy_ratings["sources"]["test"]
    assert "quality_score" not in legacy_ratings["sources"]["test"]
    assert "last_wheel_post_at" in legacy_ratings["sources"]["test"]
    legacy_ratings["sources"]["test"]["wheel_posts"] = 1
    assert not data_store.apply_source_rating_epoch(legacy_ratings)
    assert legacy_ratings["sources"]["test"]["wheel_posts"] == 1

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
