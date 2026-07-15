from __future__ import annotations

from datetime import timedelta
from typing import Any

import bbvg_monitor_runtime as runtime
import bot_notification_state
import notification_navigation
import notification_preferences_v2
import notification_router
import personal_reminder_filter
import rating_policy
import recurring_wheel_events
import telegram_post_links_v2
import telegram_transport
import wheel_lifecycle_v2
import wheel_metadata_quality
import wheel_publications_v2


monitor = runtime.monitor
notification_router.load_config = bot_notification_state.load_config
notification_preferences_v2.install(notification_router)
recurring_wheel_events.install(monitor, runtime.base_runtime)
telegram_transport.install(monitor)
telegram_post_links_v2.install(monitor)
wheel_metadata_quality.install(monitor, runtime)
wheel_publications_v2.install(monitor, runtime)
_original_recover_deadline = runtime.base_runtime._recover_deadline
_original_markup = monitor.wheel_reply_markup
_original_process_active = monitor.process_active_wheels
_original_send_message = monitor.send_message
_original_load_stats = monitor.data_store.load_stats
_original_record_admin_wheel_decision = monitor.data_store.record_admin_wheel_decision


# Error notifications are produced by system_checks.py and deduplicated in
# incident_state.json. The five-minute worker must not repeat the same warning.
monitor.all_failed_alert_due = lambda state: False


# The continuously running Telegram panel is the only callback consumer. The
# monitor must not race it for menu and participation button updates.
monitor.BOT_FEEDBACK_ENABLED = False


def load_stats_additive() -> dict[str, Any]:
    data = _original_load_stats()
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
        deadline = monitor.parse_datetime(manual.get("deadline"))
        if deadline:
            return deadline
    if str(entry.get("deadline_source") or "") == "manual":
        deadline = monitor.parse_datetime(entry.get("deadline"))
        if deadline:
            return deadline
    return _original_recover_deadline(state, normalized, entry)


def wheel_markup_with_direct_key(state, message, link, **kwargs):
    markup = _original_markup(state, message, link, **kwargs)
    key = monitor.wheel_key(link)
    for row in markup.get("inline_keyboard", []):
        for button in row:
            callback = str(button.get("callback_data") or "")
            if callback.startswith("bb:x:"):
                button["callback_data"] = f"bb:x:{key}"
            elif callback.startswith("bb:t:"):
                button["callback_data"] = f"bb:t:{key}"
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
        minimum = current + timedelta(days=runtime.MANUAL_WHEEL_TTL_DAYS)
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

# Production refresh: final reminder, strict deadline cleanup and multi-source credit.


if __name__ == "__main__":
    raise SystemExit(monitor.main())
