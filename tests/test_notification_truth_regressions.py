from __future__ import annotations

from datetime import datetime, timedelta, timezone

import betboom_participation_browser as browser
import monitor_entry
import notification_router

UTC = timezone.utc


def test_fresh_inactive_api_result_is_still_reported_as_preliminary() -> None:
    now = datetime.now(UTC)
    message = monitor_entry.monitor.Message(
        source="collector",
        message_id=1,
        date=now - timedelta(minutes=1),
        text="https://betboom.ru/freestream/example",
        message_url="https://telegram.me/collector/1",
    )
    result = monitor_entry.monitor.WheelAssessment(
        False,
        None,
        "BetBoom временно вернул inactive",
        "inactive",
        action_id=None,
        verification_status=monitor_entry.monitor.WHEEL_VERIFICATION_CONFIRMED,
        server_start_at=now,
    )

    guarded = monitor_entry._notification_first(message, result)

    assert guarded.should_notify is True
    assert guarded.status == "preliminary"
    assert "inactive" in guarded.method


def test_post_click_layout_is_not_confirmation_when_authentication_is_visible() -> None:
    assert browser._accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
        authentication_required=False,
    )
    assert not browser._accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
        authentication_required=True,
    )


def test_explicit_notification_kind_has_priority_and_scope_resets() -> None:
    text = "⚠️ Ошибка в тексте пользовательской карточки"
    assert notification_router.notification_kind(text) == "admin_system"
    with notification_router.notification_kind_scope(
        notification_router.KIND_WHEELS
    ):
        assert notification_router.resolve_notification_kind(text) == "wheels"
    assert notification_router.resolve_notification_kind(text) == "admin_system"


def test_cross_source_wheel_markup_keeps_canonical_event_marker(monkeypatch) -> None:
    import bbvg_monitor_runtime as runtime

    canonical = "evt:95a8ef357df4144a2ae1"

    def original_markup(state, message, link, **kwargs):
        token = runtime.monitor.button_context_token(message, link)
        return {
            "inline_keyboard": [
                [{"text": "open", "url": link}],
                [{"text": "join", "callback_data": f"bb:p:{token}"}],
                [{"text": "post", "url": message.message_url}],
            ],
            "_bbvg_event_id": canonical,
        }

    monkeypatch.setattr(runtime, "_original_wheel_reply_markup", original_markup)
    monkeypatch.setattr(runtime.monitor, "infer_deadline", lambda text, date: (None, ""))

    first = runtime.monitor.Message(
        source="private_2445382077",
        message_id=7815,
        date=datetime(2026, 9, 6, 14, 15, 53, tzinfo=UTC),
        text="https://betboom.ru/freestream/zonertw4",
        message_url="https://telegram.me/c/2445382077/7815",
    )
    second = runtime.monitor.Message(
        source="amam0610",
        message_id=72466,
        date=datetime(2026, 9, 6, 14, 16, 19, tzinfo=UTC),
        text="https://betboom.ru/freestream/zonertw4",
        message_url="https://telegram.me/amam0610/72466",
    )
    state = {}
    first_markup = runtime.wheel_reply_markup_bbvg(
        state, first, first.text, active=False, status="preliminary", method="test"
    )
    second_markup = runtime.wheel_reply_markup_bbvg(
        state, second, second.text, active=False, status="preliminary", method="test"
    )

    assert first_markup["_bbvg_event_id"] == canonical
    assert second_markup["_bbvg_event_id"] == canonical
    first_identity = notification_router.notification_event_identity(
        notification_router.KIND_WHEELS, first.text, None, first_markup
    )
    second_identity = notification_router.notification_event_identity(
        notification_router.KIND_WHEELS, second.text, None, second_markup
    )
    assert first_identity == second_identity
    assert canonical in first_identity


def test_private_transport_alias_is_not_shown_to_user() -> None:
    import bbvg_monitor_runtime as runtime

    private_text = (
        'Источник: <a href="https://telegram.me/c/2445382077/7815">'
        '@private_2445382077</a>'
    )
    masked = runtime._mask_private_source_handles(private_text)
    assert "@private_2445382077" not in masked
    assert "закрытый Telegram-канал" in masked
    assert "https://telegram.me/c/2445382077/7815" in masked
    assert runtime._mask_private_source_handles("Источник: @amam0610") == "Источник: @amam0610"


def test_many_sources_of_one_generation_share_one_delivery_identity(monkeypatch) -> None:
    """A high-overlap source inventory must still yield one primary card."""

    import bbvg_monitor_runtime as runtime

    canonical = "evt:1234567890abcdef1234"

    def original_markup(state, message, link, **kwargs):
        token = runtime.monitor.button_context_token(message, link)
        return {
            "inline_keyboard": [
                [{"text": "open", "url": link}],
                [{"text": "join", "callback_data": f"bb:p:{token}"}],
                [{"text": "post", "url": message.message_url}],
            ],
            "_bbvg_event_id": canonical,
        }

    monkeypatch.setattr(runtime, "_original_wheel_reply_markup", original_markup)
    monkeypatch.setattr(runtime.monitor, "infer_deadline", lambda text, date: (None, ""))

    sources = [
        "bbwheel",
        "BBfreestream",
        "shadowkekw",
        "burdakekw",
        "amam0610",
        "mechanogun",
        "betboomru",
        "betboom_baza",
        "private_2445382077",
        "hoochcs2",
        "ct0mislove",
        "zont1x",
    ]
    identities: set[str] = set()
    delivery_keys: set[str] = set()
    for index, source in enumerate(sources, 1):
        link = "https://betboom.ru/freestream/highoverlap"
        message = runtime.monitor.Message(
            source=source,
            message_id=1000 + index,
            date=datetime(2026, 9, 6, 16, 0, index, tzinfo=UTC),
            text=link,
            message_url=f"https://telegram.me/{source}/{1000 + index}",
        )
        markup = runtime.wheel_reply_markup_bbvg(
            {}, message, link, active=False, status="preliminary", method="stress"
        )
        identity = notification_router.notification_event_identity(
            notification_router.KIND_WHEELS,
            f"🎡 Новое колесо BetBoom\nИдентификатор: <code>highoverlap</code>",
            link,
            markup,
        )
        identities.add(identity)
        delivery_keys.add(
            notification_router.delivery_key(
                "owner-chat",
                notification_router.KIND_WHEELS,
                identity,
                None,
            )
        )

    assert identities == {
        "wheel:wheels:highoverlap:detected:evt:1234567890abcdef1234"
    }
    assert len(delivery_keys) == 1


def test_reused_wheel_link_with_new_generation_is_not_suppressed() -> None:
    text = "🎡 Новое колесо BetBoom\nИдентификатор: <code>highoverlap</code>"
    first = notification_router.notification_event_identity(
        notification_router.KIND_WHEELS,
        text,
        "https://betboom.ru/freestream/highoverlap",
        {
            "_bbvg_event_id": "evt:11111111111111111111",
            "inline_keyboard": [],
        },
    )
    second = notification_router.notification_event_identity(
        notification_router.KIND_WHEELS,
        text,
        "https://betboom.ru/freestream/highoverlap",
        {
            "_bbvg_event_id": "evt:22222222222222222222",
            "inline_keyboard": [],
        },
    )

    assert first != second
    assert notification_router.delivery_key(
        "owner-chat", notification_router.KIND_WHEELS, first, None
    ) != notification_router.delivery_key(
        "owner-chat", notification_router.KIND_WHEELS, second, None
    )


def test_high_overlap_sources_are_present_once_in_inventory() -> None:
    from pathlib import Path

    values = [
        line.strip().lstrip("@")
        for line in (Path(__file__).resolve().parents[1] / "public_sources.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    folded = [value.casefold() for value in values]
    assert "bbwheel" in folded
    assert "bbfreestream" in folded
    assert len(folded) == len(set(folded))
