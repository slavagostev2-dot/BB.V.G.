from __future__ import annotations

from typing import Any, Callable

import notification_delivery_guard


def _source(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _message_id(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def publication_identity(message: Any) -> tuple[str, int, str]:
    return (
        _source(getattr(message, "source", "")),
        _message_id(getattr(message, "message_id", 0)),
        str(getattr(message, "message_url", "") or "").strip(),
    )


def _same_publication(row: Any, identity: tuple[str, int, str]) -> bool:
    if not isinstance(row, dict):
        return False
    source, message_id, message_url = identity
    row_source = _source(row.get("source"))
    row_id = _message_id(row.get("message_id"))
    row_url = str(row.get("message_url") or "").strip()
    if source and message_id and row_source == source and row_id == message_id:
        return True
    return bool(message_url and row_url and row_url == message_url)


def publication_already_known(
    monitor_module: Any,
    state: dict[str, Any] | None,
    message: Any,
    link: str,
) -> bool:
    if not isinstance(state, dict):
        return False
    try:
        key = monitor_module.wheel_key(link)
    except Exception:
        return False
    identity = publication_identity(message)

    rows = state.get("wheel_publications", {}).get(key, [])
    if isinstance(rows, list) and any(_same_publication(row, identity) for row in rows):
        return True

    for collection_name in (
        "active_wheels",
        "recently_completed_wheels",
        "inactive_wheels",
    ):
        entry = state.get(collection_name, {}).get(key)
        if _same_publication(entry, identity):
            return True
    return False


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_restart_duplicate_guard_installed", False):
        notification_delivery_guard.install(monitor_module)
        return

    original_new: Callable = monitor_module.assess_new_wheel
    original_pending: Callable = monitor_module.assess_pending_wheel

    def assess_new_once(message: Any, link: str, state: dict | None = None):
        if publication_already_known(monitor_module, state, message, link):
            return monitor_module.WheelAssessment(
                False,
                None,
                "этот Telegram-пост уже был обработан ранее",
                "duplicate_publication",
                "",
            )
        return original_new(message, link, state)

    def assess_pending_once(message: Any, link: str, state: dict | None = None):
        if publication_already_known(monitor_module, state, message, link):
            return monitor_module.WheelAssessment(
                False,
                None,
                "этот Telegram-пост уже был обработан ранее",
                "duplicate_publication",
                "",
            )
        return original_pending(message, link, state)

    monitor_module.assess_new_wheel = assess_new_once
    monitor_module.assess_pending_wheel = assess_pending_once
    monitor_module._bbvg_restart_duplicate_guard_installed = True
    notification_delivery_guard.install(monitor_module)


def self_test() -> None:
    import monitor

    message = monitor.Message(
        source="official",
        message_id=123,
        date=monitor.now_utc(),
        text="https://betboom.ru/freestream/test",
        message_url="https://telegram.me/official/123",
    )
    state = {
        "wheel_publications": {
            "test": [
                {
                    "source": "official",
                    "message_id": 123,
                    "message_url": "https://telegram.me/official/123",
                }
            ]
        }
    }
    assert publication_already_known(
        monitor, state, message, "https://betboom.ru/freestream/test"
    )
    newer = monitor.Message(
        source="official",
        message_id=124,
        date=monitor.now_utc(),
        text=message.text,
        message_url="https://telegram.me/official/124",
    )
    assert not publication_already_known(
        monitor, state, newer, "https://betboom.ru/freestream/test"
    )
    print("restart duplicate guard self-test passed")


if __name__ == "__main__":
    self_test()
