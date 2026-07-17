from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import bbvg_monitor_runtime as runtime
import admin_action_queue
import bot_notification_state
import notification_navigation
import notification_preferences_v2
import notification_router
import personal_reminder_filter
import personal_wheel_voting
import rating_policy
import recurring_wheel_events
import restart_duplicate_guard
import telegram_post_links_v2
import telegram_transport
import wheel_lifecycle_v2
import wheel_event_runtime
import wheel_link_lifecycle
import wheel_metadata_quality
import wheel_publications_v2


monitor = runtime.monitor
notification_router.load_config = bot_notification_state.load_config
notification_preferences_v2.install(notification_router)
personal_wheel_voting.install_notification_router(notification_router)
recurring_wheel_events.install(monitor, runtime.base_runtime)
telegram_transport.install(monitor)
telegram_post_links_v2.install(monitor)
wheel_event_runtime.install(monitor, runtime)
wheel_metadata_quality.install(monitor, runtime)
wheel_publications_v2.install(monitor, runtime)
restart_duplicate_guard.install(monitor)
wheel_link_lifecycle.install(monitor)
# The legacy monitor workflow validates the publication integration point by
# function module name. Keep that stable while the installed lifecycle flag
# identifies the active timer-aware implementation.
monitor.is_suppressed.__module__ = "wheel_publications_v2"
monitor.is_activation_suppressed.__module__ = "wheel_publications_v2"
_original_recover_deadline = runtime.base_runtime._recover_deadline
_original_markup = monitor.wheel_reply_markup
_original_process_active = monitor.process_active_wheels
_original_send_message = monitor.send_message
_original_load_stats = monitor.data_store.load_stats
_original_record_admin_wheel_decision = monitor.data_store.record_admin_wheel_decision

SOURCE_RATING_RESET_DAY = "2026-07-17"
SOURCE_RATING_RESET_VERSION = 2
SOURCE_RATING_RESET_TIMEZONE = ZoneInfo("Asia/Barnaul")
SOURCE_RATING_RESET_FIELDS = (
    "wheel_posts",
    "admin_confirmed_wheels",
    "admin_rejected_wheels",
    "quality_score",
    "quality_decisions",
    "activation_sent",
    "personal_vote_points",
    "personal_vote_score",
    "personal_votes",
    "user_votes",
    "admin_votes",
    "last_vote_at",
)


# Error notifications are produced by system_checks.py and deduplicated in
# incident_state.json. The five-minute worker must not repeat the same warning.
monitor.all_failed_alert_due = lambda state: False

# Routine monitor health is available from the panel on demand. Do not send
# a periodic Telegram status message when there is no actionable event.
monitor.automatic_status_due = lambda state: False


# The continuously running Telegram panel is the only callback consumer. The
# monitor must not race it for menu and participation button updates.
monitor.BOT_FEEDBACK_ENABLED = False
monitor.process_admin_actions = admin_action_queue.process_pending


def reset_source_rating_epoch(
    data: dict[str, Any],
    *,
    at: datetime | None = None,
) -> bool:
    """Start the public source rating from zero at the requested local day."""

    if (
        data.get("source_rating_epoch_day") == SOURCE_RATING_RESET_DAY
        and int(data.get("source_rating_reset_version", 0) or 0)
        >= SOURCE_RATING_RESET_VERSION
    ):
        return False

    data.pop("admin_wheel_decisions", None)
    data.pop("personal_wheel_votes", None)

    for entry in data.setdefault("sources", {}).values():
        if not isinstance(entry, dict):
            continue
        for field in SOURCE_RATING_RESET_FIELDS:
            entry.pop(field, None)

    for daily_entry in data.setdefault("daily", {}).values():
        if not isinstance(daily_entry, dict):
            continue
        totals = daily_entry.setdefault("totals", {})
        if isinstance(totals, dict):
            for field in SOURCE_RATING_RESET_FIELDS:
                totals.pop(field, None)
        for entry in daily_entry.setdefault("sources", {}).values():
            if not isinstance(entry, dict):
                continue
            for field in SOURCE_RATING_RESET_FIELDS:
                entry.pop(field, None)

    current = (at or monitor.now_utc()).astimezone(SOURCE_RATING_RESET_TIMEZONE)
    data["source_rating_policy"] = personal_wheel_voting.PERSONAL_RATING_POLICY
    data["source_rating_epoch_day"] = SOURCE_RATING_RESET_DAY
    data["source_rating_reset_version"] = SOURCE_RATING_RESET_VERSION
    data["source_rating_reset_at"] = current.isoformat()
    data["source_rating_counting_from"] = (
        f"{SOURCE_RATING_RESET_DAY}T00:00:00+07:00"
    )
    return True


def load_stats_additive() -> dict[str, Any]:
    data = _original_load_stats()
    if reset_source_rating_epoch(data):
        monitor.data_store.save_stats(data)
    if data.get("source_rating_policy") != personal_wheel_voting.PERSONAL_RATING_POLICY:
        rating_policy.normalize_additive_rating(data)
    return data


def record_admin_wheel_decision_additive(
    data: dict[str, Any],
    *,
    wheel_key: str,
    sources: list[str],
    decision: str,
    actor: str = "admin",
    at: Any = None,
) -> bool:
    return rating_policy.record_admin_wheel_decision(
        data,
        wheel_key=wheel_key,
        sources=sources,
        decision=decision,
        actor=actor,
        at=at,
        recorder=_original_record_admin_wheel_decision,
    )


def recover_deadline_manual_first(state: dict, key: str, entry: dict):
    normalized = str(key or "").casefold()
    manual = state.get("manual_deadlines", {}).get(normalized)
    if isinstance(manual, dict):
        updated_at = monitor.parse_datetime(manual.get("updated_at"))
        message_at = monitor.parse_datetime(entry.get("message_date"))
        deadline = monitor.parse_datetime(manual.get("deadline"))
        same_event = message_at is None or (
            updated_at is not None and updated_at >= message_at
        )
        if deadline and same_event:
            return deadline
    if str(entry.get("deadline_source") or "") == "manual":
        deadline = monitor.parse_datetime(entry.get("deadline"))
        if deadline:
            return deadline
    if (
        str(entry.get("verification_status") or "")
        == monitor.WHEEL_VERIFICATION_CONFIRMED
        and str(entry.get("deadline_source") or "") == "api_missing"
    ):
        return None
    return _original_recover_deadline(state, normalized, entry)


def wheel_markup_with_direct_key(state, message, link, **kwargs):
    markup = _original_markup(state, message, link, **kwargs)
    key = monitor.wheel_key(link)
    cleaned_rows: list[list[dict[str, Any]]] = []
    for row in markup.get("inline_keyboard", []):
        if not isinstance(row, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for button in row:
            if not isinstance(button, dict):
                continue
            callback = str(button.get("callback_data") or "")
            if callback.startswith(("bb:x:", "wheel:inactive:", "wheel:finished:")):
                continue
            item = dict(button)
            if callback.startswith("bb:t:"):
                item["callback_data"] = f"bb:t:{key}"
            if callback.startswith(("bb:p:", "wheel:part:")):
                item["text"] = "✅ Участвую"
            cleaned.append(item)
        if cleaned:
            cleaned_rows.append(cleaned)
    markup["inline_keyboard"] = cleaned_rows
    callbacks = {
        str(button.get("callback_data") or "")
        for row in markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict)
    }
    if f"bb:t:{key}" not in callbacks:
        rows = markup.setdefault("inline_keyboard", [])
        rows.insert(
            max(1, len(rows) - 1),
            [{"text": "⏱ Указать время", "callback_data": f"bb:t:{key}"}],
        )
    return markup


def branded_send_message(text: str, url=None, reply_markup=None):
    value = str(text or "")
    if (
        "⚠️ <b>Монитор не смог проверить ни один Telegram-источник</b>" in value
        and telegram_transport.outage_active()
    ):
        value = (
            "⚠️ <b>Временная сетевая ошибка GitHub Actions</b>\n\n"
            f"GitHub Runner не смог подключиться к <code>{telegram_transport.PRIMARY_DOMAIN}</code>.\n"
            "Источники не признаны недоступными и не отправлены в карантин.\n"
            "BB V.G. автоматически повторит проверку."
        )
    else:
        value = value.replace(
            "🤖 <b>Автоматический монитор работает</b>",
            "🤖 <b>BB V.G. работает</b>",
        )
        value = value.replace(
            "⚠️ <b>Монитор не смог проверить ни один Telegram-источник</b>",
            "⚠️ <b>BB V.G. не смог проверить ни один Telegram-источник</b>",
        )
        value = value.replace(
            "✅ <b>Ручная проверка завершена</b>",
            "✅ <b>Ручная проверка BB V.G. завершена</b>",
        )
        value = value.replace(
            "Повторная проверка одного поста проходит без сообщений.",
            "Колёса без найденного времени ожидают ручного ввода администратора.",
        )
        value = "\n".join(
            line
            for line in value.splitlines()
            if not line.startswith("Ожидают активности:")
        )
    return _original_send_message(value, url=url, reply_markup=reply_markup)


def process_active_without_unknown_time_spam(state: dict, stats: dict):
    """Unknown-time wheels wait silently until an administrator supplies a deadline."""
    changed = False
    current = monitor.now_utc()
    for entry in state.setdefault("active_wheels", {}).values():
        if not isinstance(entry, dict):
            continue
        if monitor.parse_datetime(entry.get("deadline")) is not None:
            continue
        if not bool(entry.get("needs_manual_time")):
            entry["needs_manual_time"] = True
            changed = True
        if not entry.get("manual_time_waiting_since"):
            entry["manual_time_waiting_since"] = current.isoformat()
            changed = True
        suppress_until = monitor.parse_datetime(entry.get("last_unknown_reminder_at"))
        minimum = runtime._entry_untimed_expiry(entry, current)
        if suppress_until is None or suppress_until < minimum:
            entry["last_unknown_reminder_at"] = minimum.isoformat()
            changed = True

    result = _original_process_active(state, stats)
    if changed:
        result["changed"] = True
    return result


runtime.base_runtime._recover_deadline = recover_deadline_manual_first
monitor.data_store.load_stats = load_stats_additive
monitor.data_store.record_admin_wheel_decision = record_admin_wheel_decision_additive
monitor.wheel_reply_markup = wheel_markup_with_direct_key
monitor.process_active_wheels = process_active_without_unknown_time_spam
monitor.send_message = branded_send_message
notification_navigation.install(monitor)
wheel_lifecycle_v2.install(monitor)
personal_reminder_filter.install(monitor, notification_router)

# Production refresh: personal event-scoped voting, strict API cleanup and
# multi-source credit.


if __name__ == "__main__":
    raise SystemExit(monitor.main())
