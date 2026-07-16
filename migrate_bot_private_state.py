from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bot_private_state
import privacy_retention
import wheel_lifecycle_v2
from migrate_private_state import latest_matching

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent
MONITOR_STATE_PATH = ROOT / "state.json"


def _default_access() -> dict[str, Any]:
    return {
        "version": 3,
        "owner_id": "",
        "admins": [],
        "users": {},
        "blocked_users": [],
        "notification_recipients": [],
        "settings": {
            "public_panel": True,
            "notifications": True,
            "monitor_interval_minutes": 5,
        },
    }


def _ensure_owner(access: dict[str, Any]) -> dict[str, Any]:
    result = _default_access()
    result.update(access if isinstance(access, dict) else {})
    result["admins"] = [str(value) for value in result.get("admins", []) if str(value)]
    result["blocked_users"] = [str(value) for value in result.get("blocked_users", []) if str(value)]
    result["notification_recipients"] = [
        str(value) for value in result.get("notification_recipients", []) if str(value)
    ]
    users = result.get("users")
    result["users"] = users if isinstance(users, dict) else {}
    settings = result.get("settings")
    merged_settings = _default_access()["settings"]
    if isinstance(settings, dict):
        merged_settings.update(settings)
    result["settings"] = merged_settings

    owner_id = str(
        result.get("owner_id")
        or os.getenv("ADMIN_USER_ID")
        or os.getenv("BOT_CHAT_ID")
        or ""
    ).strip()
    chat_id = str(os.getenv("BOT_CHAT_ID") or owner_id).strip()
    if owner_id:
        result["owner_id"] = owner_id
        now = datetime.now(UTC).isoformat()
        previous = result["users"].get(owner_id)
        previous = previous if isinstance(previous, dict) else {}
        result["users"][owner_id] = {
            **previous,
            "id": owner_id,
            "chat_id": str(previous.get("chat_id") or chat_id or owner_id),
            "username": str(previous.get("username") or ""),
            "first_name": str(previous.get("first_name") or "Администратор"),
            "last_name": str(previous.get("last_name") or ""),
            "first_seen_at": str(previous.get("first_seen_at") or now),
            "last_seen_at": str(previous.get("last_seen_at") or now),
            "notifications_enabled": True,
        }
        recipient = str(result["users"][owner_id].get("chat_id") or owner_id)
        recipients = {str(value) for value in result["notification_recipients"] if str(value)}
        recipients.add(recipient)
        result["notification_recipients"] = sorted(recipients)
    result["version"] = 3
    return result


def migrate_creator_participation(
    bundle: dict[str, Any], monitor_state: dict[str, Any]
) -> int:
    """Preserve legacy shared marks only for the creator who made them."""

    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    owner_id = str(access.get("owner_id") or "")
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    owner = users.get(owner_id) if owner_id else None
    shared = (
        monitor_state.get("participating_wheels")
        if isinstance(monitor_state.get("participating_wheels"), dict)
        else {}
    )
    archived = (
        monitor_state.get("legacy_creator_participation_archive")
        if isinstance(monitor_state.get("legacy_creator_participation_archive"), dict)
        else {}
    )
    legacy = {**archived, **shared}
    if not isinstance(owner, dict) or not legacy:
        return 0
    raw_personal = owner.get("participating_wheels")
    if isinstance(raw_personal, dict):
        personal = dict(raw_personal)
    elif isinstance(raw_personal, list):
        personal = {str(value).casefold(): {} for value in raw_personal if str(value)}
    else:
        personal = {}
    changed = 0
    now = datetime.now(UTC).isoformat()
    for key, entry in legacy.items():
        normalized = str(key or "").casefold()
        if not normalized or normalized in personal:
            continue
        marked_at = str(entry.get("marked_at") or now) if isinstance(entry, dict) else now
        personal[normalized] = {
            "joined_at": marked_at,
            "migrated_from_legacy_shared_state": True,
        }
        changed += 1
    if changed:
        owner["participating_wheels"] = personal
    return changed


def load_monitor_state() -> dict[str, Any]:
    try:
        value = json.loads(MONITOR_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def save_monitor_state(value: dict[str, Any]) -> None:
    temporary = MONITOR_STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(MONITOR_STATE_PATH)


def finalize_legacy_participation_state(
    bundle: dict[str, Any],
    monitor_state: dict[str, Any],
    *,
    persist: bool = True,
) -> bool:
    """Clear shared marks only after the creator's private copy is durable."""

    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    owner_id = str(access.get("owner_id") or "")
    owner = access.get("users", {}).get(owner_id) if owner_id else None
    personal = owner.get("participating_wheels") if isinstance(owner, dict) else None
    personal_keys = (
        {str(key).casefold() for key in personal}
        if isinstance(personal, (dict, list))
        else set()
    )
    shared = monitor_state.get("participating_wheels")
    archive = monitor_state.get("legacy_creator_participation_archive")
    legacy_keys = {
        str(key).casefold()
        for collection in (shared, archive)
        if isinstance(collection, dict)
        for key in collection
    }
    if not legacy_keys or not legacy_keys.issubset(personal_keys):
        return False
    changed = bool(wheel_lifecycle_v2.migrate_legacy_global_participation(monitor_state))
    if "legacy_creator_participation_archive" in monitor_state:
        monitor_state.pop("legacy_creator_participation_archive", None)
        changed = True
    if changed and persist:
        save_monitor_state(monitor_state)
    return changed


def build_bundle() -> dict[str, Any]:
    legacy_access = latest_matching(
        "bot_access.json",
        lambda value: bool(value.get("users")) or bool(value.get("owner_id")),
        _default_access(),
    )
    source_requests = latest_matching(
        "source_requests.json",
        lambda value: bool(value.get("requests")),
        {"version": 1, "requests": {}},
    )
    bundle = bot_private_state.default_bundle(
        _ensure_owner(legacy_access),
        source_requests,
    )
    privacy_retention.prune_bundle(bundle)
    return bundle


def migrate(force: bool = False) -> tuple[bool, int, int, str]:
    """Migrate immediately to AES-GCM v2, then rotate to a dedicated key when available."""

    raw_text = ""
    try:
        raw_text = bot_private_state.STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass

    if raw_text:
        current = bot_private_state.load_text(
            raw_text,
            access_default={},
            source_requests_default={"version": 1, "requests": {}},
        )
        current_format = bot_private_state.state_format(raw_text)
        current_key_mode = bot_private_state.state_key_mode(raw_text)
    else:
        current = bot_private_state.default_bundle({}, {"version": 1, "requests": {}})
        current_format = "missing"
        current_key_mode = "missing"

    current_access = current.get("access") if isinstance(current.get("access"), dict) else {}
    populated = bool(current_access.get("owner_id") and current_access.get("users"))

    if populated:
        monitor_state = load_monitor_state()
        retention_changed = privacy_retention.prune_bundle(current)
        creator_marks_migrated = migrate_creator_participation(
            current, monitor_state
        )
        should_upgrade = current_format != bot_private_state.FORMAT_V2
        should_rotate_key = (
            bot_private_state.dedicated_key_configured()
            and (
                current_key_mode != "dedicated"
                or bot_private_state.previous_key_configured()
            )
        )
        should_reseal = bool(
            force
            or retention_changed
            or creator_marks_migrated
            or should_upgrade
            or should_rotate_key
        )
        if should_reseal:
            # Without BOT_STATE_KEY, save_file writes AES-GCM v2 in temporary
            # bot_token_compat mode. This removes legacy cryptography immediately
            # while preserving a safe later rotation to the dedicated key.
            bot_private_state.save_file(current)
            current_format = bot_private_state.FORMAT_V2
            changed = True
        else:
            changed = False
        finalize_legacy_participation_state(current, monitor_state)
        users = len(current_access.get("users") or {})
        requests = len((current.get("source_requests") or {}).get("requests") or {})
        return changed, users, requests, current_format

    bundle = build_bundle()
    monitor_state = load_monitor_state()
    migrate_creator_participation(bundle, monitor_state)
    bot_private_state.save_file(bundle)
    finalize_legacy_participation_state(bundle, monitor_state)
    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    requests_state = (
        bundle.get("source_requests")
        if isinstance(bundle.get("source_requests"), dict)
        else {}
    )
    return (
        True,
        len(access.get("users") or {}),
        len(requests_state.get("requests") or {}),
        bot_private_state.FORMAT_V2,
    )


def self_test() -> None:
    sample = bot_private_state.default_bundle(
        _ensure_owner({"users": {}}),
        {"version": 1, "requests": {}},
    )
    encoded = bot_private_state.seal(sample, "test-secret")
    decoded = bot_private_state.unseal(encoded, "test-secret")
    assert decoded == sample
    assert bot_private_state.state_format(encoded) == bot_private_state.FORMAT_V2
    assert decoded["access"]["settings"]["public_panel"] is True
    migration = bot_private_state.default_bundle(
        {
            "owner_id": "1",
            "users": {"1": {"participating_wheels": {}}},
        },
        {"version": 1, "requests": {}},
    )
    moved = migrate_creator_participation(
        migration,
        {
            "participating_wheels": {
                "wheel-a": {"marked_at": "2026-07-16T10:00:00+00:00"},
                "wheel-b": {"marked_at": "2026-07-16T10:01:00+00:00"},
            }
        },
    )
    assert moved == 2
    assert set(migration["access"]["users"]["1"]["participating_wheels"]) == {
        "wheel-a",
        "wheel-b",
    }
    monitor_state = {
        "active_wheels": {
            "wheel-a": {"identifier": "wheel-a", "participating": True},
            "wheel-b": {"identifier": "wheel-b", "participating": True},
        },
        "participating_wheels": {
            "wheel-a": {"identifier": "wheel-a"},
            "wheel-b": {"identifier": "wheel-b"},
        },
    }
    assert finalize_legacy_participation_state(
        migration, monitor_state, persist=False
    )
    assert not monitor_state["participating_wheels"]
    assert "legacy_creator_participation_archive" not in monitor_state
    assert set(monitor_state["admin_confirmed_wheels"]) == {"wheel-a", "wheel-b"}
    print("BB V.G. bot state migration v2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--require-v2", action="store_true")
    args = parser.parse_args()
    if args.check:
        self_test()
        return 0
    changed, users, requests, format_name = migrate(force=args.force)
    print(
        "Bot private state migration completed: "
        f"changed={'yes' if changed else 'no'}, users={users}, "
        f"source_requests={requests}, format={format_name}"
    )
    if args.require_v2 and format_name != bot_private_state.FORMAT_V2:
        print("Encrypted state did not migrate to AES-GCM v2.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
