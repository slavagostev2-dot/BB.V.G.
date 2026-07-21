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
    "action_id",
    "available_at",
    "availability_status",
    "verification_status",
)
_RECOVERY_METHOD_MARKERS = (
    "восстановлено аварийной проверкой BetBoom",
    "восстановлено recovery-проверкой BetBoom",
)
_PUBLICATION_FIELDS = ("source", "message_id", "message_date", "message_url")


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


def _publication_timestamp(monitor_module: Any, row: dict[str, Any]):
    parsed = monitor_module.parse_datetime(row.get("message_date"))
    if parsed is not None:
        return parsed
    return datetime.max.replace(tzinfo=timezone.utc)


def _valid_publication(row: Any) -> bool:
    if not isinstance(row, dict) or not str(row.get("source") or "").strip():
        return False
    try:
        return int(row.get("message_id") or 0) > 0
    except (TypeError, ValueError):
        return False


def repair_recovery_attribution(monitor_module: Any, state: dict[str, Any]) -> bool:
    """Restore the original publication after a recovery scan rebuilt an active card.

    Recovery is allowed to reconstruct a missing active wheel from any fresh repost,
    but it must not permanently turn that repost into the canonical source when the
    monitor already retained earlier publication evidence for the same wheel.
    Only publication fields are repaired; API timing and participation state remain
    untouched.
    """

    active = state.get("active_wheels")
    publications = state.get("wheel_publications")
    if not isinstance(active, dict) or not isinstance(publications, dict):
        return False

    changed = False
    for raw_key, entry in active.items():
        if not isinstance(entry, dict):
            continue
        method = str(entry.get("method") or "")
        if not any(marker in method for marker in _RECOVERY_METHOD_MARKERS):
            continue

        key = str(raw_key).casefold()
        rows = publications.get(key)
        if not isinstance(rows, list):
            rows = publications.get(raw_key)
        valid_rows = [
            row for row in (rows if isinstance(rows, list) else []) if _valid_publication(row)
        ]
        if not valid_rows:
            continue

        canonical = min(
            valid_rows,
            key=lambda row: _publication_timestamp(monitor_module, row),
        )
        row_changed = False
        for field in _PUBLICATION_FIELDS:
            value = canonical.get(field)
            if value in (None, "") or entry.get(field) == value:
                continue
            entry[field] = value
            row_changed = True

        if row_changed:
            entry["method"] = "исходная публикация восстановлена из wheel_publications"
            entry["metadata_quality"] = "recovery_attribution_repaired"
            changed = True

    return changed


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
    original_process_active: Callable = monitor_module.process_active_wheels

    def remember_active_preserving_quality(
        state: dict,
        message: Any,
        link: str,
        deadline: Any,
        status: str,
        method: str,
        page_excerpt: str = "",
        **metadata: Any,
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
            **metadata,
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

    def process_active_repairing_recovery(state: dict, stats: dict):
        repaired = repair_recovery_attribution(monitor_module, state)
        result = original_process_active(state, stats)
        if repaired:
            result["changed"] = True
        return result

    monitor_module.remember_active_wheel = remember_active_preserving_quality
    runtime_module.remember_without_pending = remember_pending_preserving_quality
    monitor_module.remember_pending = remember_pending_preserving_quality
    monitor_module.process_active_wheels = process_active_repairing_recovery
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

        @staticmethod
        def process_active_wheels(state, stats):
            return {"changed": False}

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

    recovery_state = {
        "active_wheels": {
            "zonertg12": {
                "source": "collector",
                "message_id": 71831,
                "message_date": "2026-07-21T09:34:22+00:00",
                "message_url": "https://telegram.me/collector/71831",
                "method": "восстановлено аварийной проверкой BetBoom",
                "participating": True,
                "action_id": 697,
            }
        },
        "wheel_publications": {
            "zonertg12": [
                {
                    "source": "mechanogun",
                    "message_id": 35659,
                    "message_date": "2026-07-21T08:06:00+00:00",
                    "message_url": "https://telegram.me/mechanogun/35659",
                },
                {
                    "source": "collector",
                    "message_id": 71831,
                    "message_date": "2026-07-21T09:34:22+00:00",
                    "message_url": "https://telegram.me/collector/71831",
                },
            ]
        },
    }
    result = FakeMonitor.process_active_wheels(recovery_state, {})
    repaired = recovery_state["active_wheels"]["zonertg12"]
    assert result["changed"] is True
    assert repaired["source"] == "mechanogun"
    assert repaired["message_id"] == 35659
    assert repaired["participating"] is True
    assert repaired["action_id"] == 697
    assert repaired["metadata_quality"] == "recovery_attribution_repaired"
    assert not repair_recovery_attribution(FakeMonitor, recovery_state)

    print("wheel_metadata_quality timed-source preservation self-test passed")


if __name__ == "__main__":
    self_test()
