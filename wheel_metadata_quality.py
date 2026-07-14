from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


_EVIDENCE_FIELDS = (
    "source",
    "message_id",
    "message_date",
    "message_url",
    "message_text",
    "status",
    "page_status",
    "method",
    "page_excerpt",
    "button_token",
    "deadline_source",
)


def _future_deadline(monitor_module: Any, entry: Any):
    if not isinstance(entry, dict):
        return None
    deadline = monitor_module.parse_datetime(entry.get("deadline"))
    if deadline is None or deadline <= monitor_module.now_utc():
        return None
    return deadline


def _restore_timed_evidence(
    monitor_module: Any,
    state: dict,
    key: str,
    previous: dict[str, Any] | None,
) -> bool:
    """Restore a still-valid timed record after a poorer repost overwrote it."""

    previous_deadline = _future_deadline(monitor_module, previous)
    if previous_deadline is None:
        return False

    current = state.setdefault("active_wheels", {}).get(key)
    if not isinstance(current, dict):
        return False
    if monitor_module.parse_datetime(current.get("deadline")) is not None:
        return False

    for field in _EVIDENCE_FIELDS:
        value = previous.get(field) if isinstance(previous, dict) else None
        if value not in (None, ""):
            current[field] = value

    current["deadline"] = previous_deadline.isoformat()
    current["expires_at"] = monitor_module.participation_expiry(
        previous_deadline,
        current=monitor_module.now_utc(),
    ).isoformat()
    current["needs_manual_time"] = False
    current["last_checked_at"] = monitor_module.now_utc().isoformat()
    current["metadata_quality"] = "preserved_timed_publication"
    return True


def install(monitor_module: Any, runtime_module: Any) -> None:
    """Prevent later posts without a timer from degrading an active wheel.

    A collector channel can publish the same wheel after the official source.
    The later post is useful as another publication, but it must not remove an
    already known future deadline or replace the richer source record.
    """

    if getattr(monitor_module, "_bbvg_wheel_metadata_quality_installed", False):
        return

    original_active: Callable = monitor_module.remember_active_wheel
    original_pending: Callable = runtime_module.remember_without_pending

    def remember_active_preserving_quality(
        state: dict,
        message: Any,
        link: str,
        deadline: Any,
        status: str,
        method: str,
        page_excerpt: str = "",
    ) -> None:
        key = monitor_module.wheel_key(link)
        raw_previous = state.setdefault("active_wheels", {}).get(key)
        previous = deepcopy(raw_previous) if isinstance(raw_previous, dict) else None
        original_active(
            state,
            message,
            link,
            deadline,
            status,
            method,
            page_excerpt,
        )
        if deadline is None:
            _restore_timed_evidence(monitor_module, state, key, previous)

    def remember_pending_preserving_quality(
        state: dict,
        post_key: str,
        message: Any,
        link: str,
        status: str,
        reason: str,
        *,
        initial_notified: bool = False,
    ) -> None:
        key = monitor_module.wheel_key(link)
        raw_previous = state.setdefault("active_wheels", {}).get(key)
        previous = deepcopy(raw_previous) if isinstance(raw_previous, dict) else None
        original_pending(
            state,
            post_key,
            message,
            link,
            status,
            reason,
            initial_notified=initial_notified,
        )
        _restore_timed_evidence(monitor_module, state, key, previous)

    monitor_module.remember_active_wheel = remember_active_preserving_quality
    runtime_module.remember_without_pending = remember_pending_preserving_quality
    monitor_module.remember_pending = remember_pending_preserving_quality
    monitor_module._bbvg_wheel_metadata_quality_installed = True


def self_test() -> None:
    utc = timezone.utc
    fixed_now = datetime(2026, 7, 14, 14, 0, tzinfo=utc)

    class FakeMonitor:
        _bbvg_wheel_metadata_quality_installed = False

        @staticmethod
        def now_utc():
            return fixed_now

        @staticmethod
        def parse_datetime(value):
            if isinstance(value, datetime):
                return value
            if not isinstance(value, str) or not value:
                return None
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=utc)

        @staticmethod
        def wheel_key(link):
            return str(link).split("/freestream/", 1)[-1].split("?", 1)[0].casefold()

        @staticmethod
        def participation_expiry(deadline, *, current=None):
            return deadline + timedelta(minutes=30)

        @staticmethod
        def remember_active_wheel(state, message, link, deadline, status, method, page_excerpt=""):
            key = FakeMonitor.wheel_key(link)
            row = dict(state.setdefault("active_wheels", {}).get(key) or {})
            row.update(
                {
                    "source": message.source,
                    "message_id": message.message_id,
                    "message_date": message.date.isoformat(),
                    "message_url": message.message_url,
                    "message_text": message.text,
                    "method": method,
                    "needs_manual_time": deadline is None,
                }
            )
            if deadline is None:
                row.pop("deadline", None)
            else:
                row["deadline"] = deadline.isoformat()
            state["active_wheels"][key] = row

    class FakeRuntime:
        @staticmethod
        def remember_without_pending(state, post_key, message, link, status, reason, *, initial_notified=False):
            FakeMonitor.remember_active_wheel(state, message, link, None, status, reason)

    class Message:
        source = "kolesaBB"
        message_id = 108
        date = fixed_now - timedelta(hours=2)
        message_url = "https://telegram.me/kolesaBB/108"
        text = "https://betboom.ru/freestream/zonertg5"

    deadline = fixed_now + timedelta(hours=4)
    original = {
        "source": "mechanogun",
        "message_id": 35606,
        "message_date": (fixed_now - timedelta(hours=7)).isoformat(),
        "message_url": "https://telegram.me/mechanogun/35606",
        "message_text": "ИТОГИ ЧЕРЕЗ 10 ЧАСОВ",
        "deadline": deadline.isoformat(),
        "needs_manual_time": False,
    }

    install(FakeMonitor, FakeRuntime)

    direct_state = {"active_wheels": {"zonertg5": deepcopy(original)}}
    FakeMonitor.remember_active_wheel(
        direct_state,
        Message(),
        "https://betboom.ru/freestream/zonertg5",
        None,
        "manual_time_required",
        "время не найдено",
    )
    direct = direct_state["active_wheels"]["zonertg5"]
    assert direct["source"] == "mechanogun"
    assert direct["deadline"] == deadline.isoformat()
    assert direct["needs_manual_time"] is False

    pending_state = {"active_wheels": {"zonertg5": deepcopy(original)}}
    FakeMonitor.remember_pending(
        pending_state,
        "post-key",
        Message(),
        "https://betboom.ru/freestream/zonertg5",
        "fresh_unconfirmed",
        "время не найдено",
    )
    pending = pending_state["active_wheels"]["zonertg5"]
    assert pending["source"] == "mechanogun"
    assert pending["deadline"] == deadline.isoformat()
    print("wheel_metadata_quality timed-source preservation self-test passed")


if __name__ == "__main__":
    self_test()
