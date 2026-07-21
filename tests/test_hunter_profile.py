from __future__ import annotations

from datetime import datetime, timezone

from bbvg.bot import profile

UTC = timezone.utc


def test_manual_and_auto_same_action_are_one_event() -> None:
    stats = {
        "personal_wheel_votes": {
            "one": {
                "actor": "vote_user",
                "wheel_key": "wheel-a",
                "event_key": "wheel-a#action:10",
                "voted_at": "2026-07-18T10:00:00+00:00",
            }
        }
    }
    state = {
        "auto_participation_events": {
            "wheel-a#action:10:2026-07-18T09:59:00+00:00": {
                "wheel_key": "wheel-a",
                "status": "participated",
                "attempted_at": "2026-07-18T09:59:00+00:00",
            }
        }
    }
    events = profile.collect_participation_events(
        stats, state, actor="vote_user", include_auto=True
    )
    assert len(events) == 1
    assert events[0]["event_key"] == "wheel-a#action:10"
    assert events[0]["method"] == "auto"


def test_generation_and_worker_event_identity_are_deduplicated() -> None:
    stats = {
        "personal_wheel_votes": {
            "one": {
                "actor": "vote_user",
                "wheel_key": "wheel-a",
                "event_key": "wheel-a#generation:abc",
                "voted_at": "2026-07-18T10:00:00+00:00",
            }
        }
    }
    state = {
        "auto_participation_events": {
            "wheel-a#event:abc": {
                "wheel_key": "wheel-a",
                "status": "already_participating",
                "attempted_at": "2026-07-18T09:59:00+00:00",
            }
        }
    }
    events = profile.collect_participation_events(
        stats, state, actor="vote_user", include_auto=True
    )
    assert len(events) == 1
    assert events[0]["event_key"] == "wheel-a#id:abc"


def test_profile_rebuilds_personal_counts_streak_and_history() -> None:
    stats = {
        "personal_wheel_votes": {
            str(index): {
                "actor": "vote_user",
                "wheel_key": f"wheel-{index}",
                "event_key": f"wheel-{index}#action:{index}",
                "voted_at": f"2026-07-{17 + index:02d}T12:00:00+00:00",
            }
            for index in range(1, 4)
        }
    }
    user = {
        "first_seen_at": "2026-07-01T00:00:00+00:00",
        "participating_wheels": {
            "wheel-3#action:3": {"wheel_key": "wheel-3"},
        },
    }
    result = profile.build_profile(
        stats,
        {"auto_participation_events": {}},
        user,
        actor="vote_user",
        include_auto=False,
        current=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )
    assert result["total"] == 3
    assert result["manual"] == 3
    assert result["auto"] == 0
    assert "active" not in result
    assert result["current_streak"] == 3
    assert result["best_streak"] == 3
    assert result["best_month"] == "2026-07"
    assert result["best_month_count"] == 3
    assert result["days_in_bot"] == 19
    assert "🔥 Серия 3 дня" in result["achievements"]

    text = profile.format_profile(result, include_auto=False)
    assert "Активные колёса" not in text
    assert "Сейчас активных" not in text
    assert "<b>Участие</b>" in text
    assert "<b>Личная активность</b>" in text


def test_auto_history_is_counted_only_when_requested() -> None:
    state = {
        "auto_participation_events": {
            "wheel-a#action:1:start": {
                "status": "participated",
                "wheel_key": "wheel-a",
                "attempted_at": "2026-07-20T10:00:00+00:00",
            }
        }
    }
    without_auto = profile.collect_participation_events(
        {}, state, actor="vote_user", include_auto=False
    )
    with_auto = profile.collect_participation_events(
        {}, state, actor="vote_user", include_auto=True
    )
    assert without_auto == []
    assert len(with_auto) == 1
    assert with_auto[0]["method"] == "auto"


def test_profile_callback_edits_same_message_and_keeps_menu_order() -> None:
    class Base:
        def compact_menu_rows(self, admin: bool):
            return [[{"text": "one"}], [{"text": "two"}]]

        def handle_callback(self, query):
            self.delegated = query.get("data")

    class Mixin:
        pass

    profile.install(Mixin)

    class Runtime(Mixin, Base):
        current_user_id = "1"
        _edit_message_id = None

        def _prepare_callback_user(self, query):
            self.prepared = True

        def answer(self, query_id, text):
            self.answered = (query_id, text)

        def show_profile(self):
            self.profile_message_id = self._edit_message_id

    runtime = Runtime()
    rows = runtime.compact_menu_rows(False)
    assert [row[0]["text"] for row in rows] == ["one", "two", "👤 Мой профиль"]

    runtime.handle_callback(
        {
            "id": "q",
            "data": "page:profile",
            "message": {"message_id": 77, "chat": {"id": "1"}},
        }
    )
    assert runtime.prepared is True
    assert runtime.profile_message_id == 77
    assert runtime._edit_message_id is None

    runtime.handle_callback(
        {
            "id": "refresh",
            "data": "profile:refresh",
            "message": {"message_id": 88, "chat": {"id": "1"}},
        }
    )
    assert runtime.profile_message_id == 88
    assert runtime._edit_message_id is None

    runtime.handle_callback({"data": "other"})
    assert runtime.delegated == "other"


def test_analytics_keeps_period_metrics_and_links_to_detail_sections() -> None:
    text = (
        "📊 <b>Аналитика за 7 дней</b>\n\n"
        "<b>Находки</b>\n"
        "🎡 Публикаций с колёсами: <b>5</b>\n"
        "⚠️ Ошибок источников: <b>2</b>\n"
        "✅ Проблемных источников сейчас: <b>0</b>\n\n"
        "<b>Участие и рейтинг</b>\n"
        "🙋 Личных голосов: <b>4</b>\n\n"
        "<b>Сейчас</b>\n"
        "🔥 Активных колёс: <b>1</b>\n\n"
        "<b>Покрытие источников</b>\n"
        "✅ Доступно: <b>168 из 168</b>"
    )
    cleaned = profile.analytics_text_for_section(text)
    assert "Публикаций с колёсами" in cleaned
    assert "Разовых ошибок проверок за период" in cleaned
    assert "Проблемных источников сейчас" not in cleaned
    assert "Участие и рейтинг" not in cleaned
    assert "Активных колёс" not in cleaned
    assert "Покрытие источников" not in cleaned

    markup = profile.analytics_markup_for_section(
        {
            "inline_keyboard": [
                [{"text": "7 дней", "callback_data": "page:analytics:7"}],
                [{"text": "Давно без колёс", "callback_data": "page:report:inactive"}],
                [{"text": "Главное меню", "callback_data": "nav:home"}],
            ]
        }
    )
    assert markup is not None
    callbacks = [
        str(button.get("callback_data") or "")
        for row in markup["inline_keyboard"]
        for button in row
    ]
    assert "page:analytics:7" in callbacks
    assert "page:report:inactive" in callbacks
    assert callbacks.count("page:ranking") == 0
    assert callbacks.count("page:sources") == 0
    assert callbacks[-1] == "nav:home"


def test_future_runtime_analytics_is_wrapped_after_class_definition() -> None:
    class Base:
        def compact_menu_rows(self, admin: bool):
            return []

        def handle_callback(self, query):
            return None

        def send(self, text, *, reply_markup=None, chat_id=None):
            self.sent = (text, reply_markup, chat_id)
            return {}

    class Mixin:
        pass

    profile.install(Mixin)

    class Runtime(Mixin, Base):
        def show_analytics(self, days=1):
            self.send(
                "Находки\n⚠️ Ошибок источников: <b>0</b>\n"
                "<b>Участие и рейтинг</b>\nЛичные голоса",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "Главное меню", "callback_data": "nav:home"}]
                    ]
                },
            )

    class Production(Runtime):
        def show_analytics(self, days=1):
            super().show_analytics(days)

    runtime = Production()
    runtime.show_analytics(7)
    text, markup, _ = runtime.sent
    assert "Находки" in text
    assert "Разовых ошибок проверок за период" in text
    assert "Участие и рейтинг" not in text
    assert markup is not None
    assert "page:ranking" not in str(markup)
    assert "page:sources" not in str(markup)
