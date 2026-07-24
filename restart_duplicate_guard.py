from __future__ import annotations

import copy
import json
import os
import subprocess
from typing import Any, Callable

import notification_delivery_guard


_RECOVERY_PENDING_FIELDS = (
    "recovered_initial_notification_pending_at",
    "recovered_initial_notification_reason",
    "recovered_initial_notification_error",
)
_RECOVERY_METADATA_FIELDS = (
    "identifier",
    "url",
    "source",
    "message_id",
    "message_date",
    "message_url",
    "message_text",
    "button_token",
    "action_id",
    "server_start_at",
    "deadline",
    "expires_at",
    "method",
    "page_status",
    "verification_status",
    "referral_restricted",
    "wheel_key",
)


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


def _pending_recovery_subset(remote_state: dict[str, Any]) -> dict[str, Any]:
    """Keep only recovery records that the live monitor must consume."""

    remote = remote_state if isinstance(remote_state, dict) else {}
    remote_active = remote.get("active_wheels")
    pending: dict[str, dict[str, Any]] = {}
    tokens: set[str] = set()
    keys: set[str] = set()
    if isinstance(remote_active, dict):
        for raw_key, raw_entry in remote_active.items():
            if not isinstance(raw_entry, dict) or not raw_entry.get(
                "recovered_initial_notification_pending_at"
            ):
                continue
            key = str(raw_key).casefold()
            pending[key] = copy.deepcopy(raw_entry)
            keys.add(key)
            token = str(raw_entry.get("button_token") or "")
            if token:
                tokens.add(token)

    subset: dict[str, Any] = {"active_wheels": pending}
    contexts = remote.get("button_contexts")
    if isinstance(contexts, dict):
        subset["button_contexts"] = {
            str(token): copy.deepcopy(value)
            for token, value in contexts.items()
            if str(token) in tokens and isinstance(value, dict)
        }
    for name in ("participating_wheels", "wheel_publications"):
        rows = remote.get(name)
        if isinstance(rows, dict):
            subset[name] = {
                str(key): copy.deepcopy(value)
                for key, value in rows.items()
                if str(key).casefold() in keys
            }
    return subset


def merge_recovered_notification_state(
    local_state: dict[str, Any],
    remote_state: dict[str, Any],
) -> dict[str, Any]:
    """Import the existing recovery notification queue into a running monitor.

    The monitor keeps ownership of its local lifecycle state. Only records carrying
    the established ``recovered_initial_notification_pending_at`` handoff marker are
    imported from ``origin/main``.
    """

    import auto_participation_bot_sync

    local = local_state if isinstance(local_state, dict) else {}
    subset = _pending_recovery_subset(remote_state)
    merged = auto_participation_bot_sync.merge_auto_participation_state(local, subset)

    remote_active = subset.get("active_wheels", {})
    active = merged.setdefault("active_wheels", {})
    if isinstance(remote_active, dict) and isinstance(active, dict):
        for key, remote_entry in remote_active.items():
            current = active.get(key)
            if not isinstance(current, dict) or not isinstance(remote_entry, dict):
                continue
            same_event = (
                str(current.get("action_id") or "")
                == str(remote_entry.get("action_id") or "")
                and str(current.get("server_start_at") or "")
                == str(remote_entry.get("server_start_at") or "")
            )
            if not same_event:
                continue
            for field in _RECOVERY_PENDING_FIELDS:
                if field in remote_entry:
                    current[field] = copy.deepcopy(remote_entry[field])
            for field in _RECOVERY_METADATA_FIELDS:
                if current.get(field) in (None, "") and remote_entry.get(field) not in (
                    None,
                    "",
                ):
                    current[field] = copy.deepcopy(remote_entry[field])
            auto_participation_bot_sync._suppress_delivered_recovery_pending(current)
    return merged


def sync_recovered_notification_state(monitor_module: Any) -> bool:
    """Refresh recovery handoffs before this monitor iteration reads state.json."""

    if str(os.getenv("GITHUB_ACTIONS") or "").casefold() != "true":
        return False
    try:
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=monitor_module.ROOT,
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        remote_text = subprocess.check_output(
            ["git", "show", "origin/main:state.json"],
            cwd=monitor_module.ROOT,
            timeout=15,
            text=True,
            stderr=subprocess.PIPE,
        )
        remote = json.loads(remote_text)
        local = json.loads(monitor_module.STATE_PATH.read_text(encoding="utf-8"))
        merged = merge_recovered_notification_state(local, remote)
        if merged == local:
            return False
        monitor_module.data_store.atomic_write_json(monitor_module.STATE_PATH, merged)
        print("Synchronized recovery notification state from origin/main")
        return True
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            "WARNING recovery notification state sync failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_restart_duplicate_guard_installed", False):
        notification_delivery_guard.install(monitor_module)
        return

    sync_recovered_notification_state(monitor_module)
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

    local = {
        "active_wheels": {
            "existing": {
                "action_id": 1,
                "server_start_at": "2026-07-24T15:00:00+00:00",
                "last_checked_at": "local-monitor-value",
            }
        }
    }
    remote = {
        "active_wheels": {
            "kekw": {
                "wheel_key": "kekw",
                "action_id": 1023,
                "server_start_at": "2026-07-24T15:41:24.741000+00:00",
                "url": "https://betboom.ru/freestream/kekw",
                "button_token": "dfc0e5836253b9",
                "recovered_initial_notification_pending_at": (
                    "2026-07-24T15:41:53.911248+00:00"
                ),
                "recovered_initial_notification_reason": (
                    "recovery_discovered_missing_event"
                ),
            }
        },
        "button_contexts": {
            "dfc0e5836253b9": {
                "wheel_key": "kekw",
                "url": "https://betboom.ru/freestream/kekw",
            }
        },
    }
    merged = merge_recovered_notification_state(local, remote)
    assert merged["active_wheels"]["existing"]["last_checked_at"] == (
        "local-monitor-value"
    )
    assert merged["active_wheels"]["kekw"][
        "recovered_initial_notification_pending_at"
    ]
    assert "dfc0e5836253b9" in merged["button_contexts"]
    print("restart duplicate guard self-test passed")


if __name__ == "__main__":
    self_test()
