from __future__ import annotations

import html
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import wheel_publications_v2


FINAL_REMINDER_BEFORE_MINUTES = max(
    1, int(os.getenv("FINAL_REMINDER_BEFORE_MINUTES", "5"))
)
ACTIVE_REMOVE_GRACE_MINUTES = max(
    0, int(os.getenv("ACTIVE_REMOVE_GRACE_MINUTES", "0"))
)
UTC = timezone.utc


def final_reminder_due(
    monitor_module: Any,
    entry: dict[str, Any],
    current: datetime | None = None,
) -> bool:
    current = current or monitor_module.now_utc()
    if monitor_module.parse_datetime(entry.get("final_reminder_sent_at")):
        return False
    deadline = monitor_module.parse_datetime(entry.get("deadline"))
    if deadline is None:
        return False
    return (
        deadline - timedelta(minutes=FINAL_REMINDER_BEFORE_MINUTES)
        <= current
        < deadline
    )


def _source_text(state: dict[str, Any], key: str, entry: dict[str, Any]) -> str:
    sources = wheel_publications_v2.publication_sources(state, key, entry)
    if not sources:
        return "неизвестно"
    return ", ".join(f"@{html.escape(source)}" for source in sources)


def _remember_completed(
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any],
    deadline: datetime,
    current: datetime,
) -> None:
    recent = state.setdefault("recently_completed_wheels", {})
    recent[key] = {
        "identifier": str(entry.get("identifier") or key),
        "url": str(entry.get("url") or ""),
        "sources": wheel_publications_v2.publication_sources(state, key, entry),
        "deadline": deadline.isoformat(),
        "removed_at": current.isoformat(),
        "expires_at": (current + timedelta(days=1)).isoformat(),
    }
    for old_key, raw in list(recent.items()):
        if not isinstance(raw, dict):
            recent.pop(old_key, None)
            continue
        expires = monitor_datetime(raw.get("expires_at"))
        if expires is not None and expires <= current:
            recent.pop(old_key, None)


def monitor_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def install(monitor_module: Any) -> None:
    """Install the user-facing final reminder and strict active-wheel lifetime."""

    if getattr(monitor_module, "_bbvg_wheel_lifecycle_v2_installed", False):
        return

    original_process: Callable = monitor_module.process_active_wheels

    def process_active_with_final_reminder(state: dict, stats: dict):
        current = monitor_module.now_utc()
        changed = False
        final_sent = 0

        for key, entry in list(state.setdefault("active_wheels", {}).items()):
            if not isinstance(entry, dict):
                continue
            normalized = str(key).casefold()
            if monitor_module.is_participating(state, normalized):
                continue
            if not final_reminder_due(monitor_module, entry, current):
                continue
            deadline = monitor_module.parse_datetime(entry.get("deadline"))
            message = monitor_module.active_entry_message(entry)
            url = str(entry.get("url") or "")
            if deadline is None or message is None or not url:
                continue
            try:
                monitor_module.send_message(
                    "🚨 <b>Напоминание о колесе BetBoom: последний шанс</b>\n\n"
                    f"Идентификатор: <code>{html.escape(str(entry.get('identifier') or normalized))}</code>\n"
                    f"Источники: {_source_text(state, normalized, entry)}\n"
                    f"⏳ Осталось: <b>{html.escape(monitor_module.human_remaining(deadline))}</b>\n\n"
                    "Вы ещё не отметили участие. Откройте колесо сейчас — оно скоро завершится.",
                    reply_markup=monitor_module.wheel_reply_markup(
                        state,
                        message,
                        url,
                        active=True,
                        status="final_reminder",
                        method=str(entry.get("method") or "final reminder"),
                        page_excerpt=str(entry.get("page_excerpt") or ""),
                    ),
                )
            except Exception as exc:
                entry["final_reminder_error"] = f"{type(exc).__name__}: {exc}"[:300]
            else:
                entry["final_reminder_sent_at"] = current.isoformat()
                entry["last_notification_at"] = current.isoformat()
                sources = wheel_publications_v2.publication_sources(
                    state, normalized, entry
                ) or [str(entry.get("source") or "unknown")]
                for source in sources:
                    monitor_module.data_store.increment_stat(
                        stats, source, "final_participation_reminders"
                    )
                final_sent += 1
                changed = True

        result = original_process(state, stats)
        current = monitor_module.now_utc()
        for key, entry in list(state.setdefault("active_wheels", {}).items()):
            if not isinstance(entry, dict):
                continue
            deadline = monitor_module.parse_datetime(entry.get("deadline"))
            if deadline is None:
                continue
            remove_at = deadline + timedelta(minutes=ACTIVE_REMOVE_GRACE_MINUTES)
            if current < remove_at:
                continue
            normalized = str(key).casefold()
            _remember_completed(state, normalized, entry, deadline, current)
            state["active_wheels"].pop(key, None)
            result["removed"] = int(result.get("removed", 0) or 0) + 1
            changed = True

        result["final_reminders"] = final_sent
        if changed:
            result["changed"] = True
        return result

    monitor_module.process_active_wheels = process_active_with_final_reminder
    monitor_module._bbvg_wheel_lifecycle_v2_installed = True


def self_test() -> None:
    class FakeMonitor:
        @staticmethod
        def now_utc() -> datetime:
            return datetime(2026, 7, 14, 12, 56, tzinfo=UTC)

        parse_datetime = staticmethod(monitor_datetime)

    entry = {"deadline": "2026-07-14T13:00:00+00:00"}
    assert final_reminder_due(FakeMonitor, entry)
    entry["final_reminder_sent_at"] = "2026-07-14T12:56:00+00:00"
    assert not final_reminder_due(FakeMonitor, entry)
    assert ACTIVE_REMOVE_GRACE_MINUTES == 0
    assert "Напоминание о колесе BetBoom" in (
        "Напоминание о колесе BetBoom: последний шанс"
    )
    print("wheel lifecycle v2 self-test passed")


if __name__ == "__main__":
    self_test()
