from __future__ import annotations

import base64
import copy
import json
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import admin_bot as legacy
import bot_private_state
import privacy_retention
from admin_panel_runtime_v3 import INTERVAL_OPTIONS
from admin_panel_v2 import DEFAULT_SETTINGS, default_access
from bbvg.bot.source_requests import default_source_requests
from bbvg.bot.sources import SourceRegistryRuntime

UTC = timezone.utc
_MISSING = object()


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _merge_value(base: Any, local: Any, remote: Any) -> Any:
    """Apply local changes to the freshest remote value.

    The function is intentionally identical in semantics to the proven v34
    three-way merge: unchanged local values accept the remote version, while
    local additions, edits and removals are replayed over it.
    """

    if local == base:
        return _clone(remote)
    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        result = _clone(remote)
        for key in set(base) | set(local):
            base_value = base.get(key, _MISSING)
            local_value = local.get(key, _MISSING)
            if local_value == base_value:
                continue
            if local_value is _MISSING:
                result.pop(key, None)
                continue
            remote_value = remote.get(key, _MISSING)
            if (
                base_value is not _MISSING
                and remote_value is not _MISSING
                and isinstance(base_value, dict)
                and isinstance(local_value, dict)
                and isinstance(remote_value, dict)
            ):
                result[key] = _merge_value(base_value, local_value, remote_value)
            else:
                result[key] = _clone(local_value)
        return result
    return _clone(local)


def _merge_set_list(base: Any, local: Any, remote: Any) -> list[str]:
    base_set = {str(value) for value in (base or []) if str(value)}
    local_set = {str(value) for value in (local or []) if str(value)}
    remote_set = {str(value) for value in (remote or []) if str(value)}
    additions = local_set - base_set
    removals = base_set - local_set
    return sorted((remote_set | additions) - removals)


class PrivateStateRuntime(SourceRegistryRuntime):
    """Encrypted private bundle with conflict-safe role preservation.

    This consolidates only the effective storage responsibilities of historic
    v25, v34 and v35. Telegram pages, menus and notification presentation remain
    in their own modules.
    """

    def __init__(self) -> None:
        super().__init__()
        self._bot_state_lock = threading.RLock()
        self._bot_bundle: dict[str, Any] | None = None
        self._bundle_baseline: dict[str, Any] | None = None

    @staticmethod
    def _bootstrap_access(value: dict[str, Any] | None = None) -> dict[str, Any]:
        result = default_access()
        if isinstance(value, dict):
            result.update(value)
        owner_id = str(
            result.get("owner_id")
            or legacy.ADMIN_USER_ID
            or legacy.BOT_CHAT_ID
            or ""
        ).strip()
        chat_id = str(legacy.BOT_CHAT_ID or owner_id).strip()
        users = result.get("users")
        result["users"] = users if isinstance(users, dict) else {}
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
            recipients = {
                str(item)
                for item in result.get("notification_recipients", [])
                if str(item)
            }
            recipients.add(str(result["users"][owner_id].get("chat_id") or owner_id))
            result["notification_recipients"] = sorted(recipients)
        settings = result.get("settings")
        settings = settings if isinstance(settings, dict) else {}
        settings.setdefault("public_panel", True)
        settings.setdefault("notifications", True)
        settings.setdefault("monitor_interval_minutes", 5)
        result["settings"] = settings
        return result

    def normalize_access(self, value: dict[str, Any]) -> dict[str, Any]:
        """Final AES-GCM-era access format, without legacy token signatures."""

        raw = value if isinstance(value, dict) else {}
        result = default_access()
        result["owner_id"] = str(raw.get("owner_id") or "")
        result["admins"] = sorted(
            {str(item) for item in raw.get("admins", []) if str(item)}
        )
        result["blocked_users"] = sorted(
            {str(item) for item in raw.get("blocked_users", []) if str(item)}
        )
        result["notification_recipients"] = sorted(
            {
                str(item)
                for item in raw.get("notification_recipients", [])
                if str(item)
            }
        )
        users = raw.get("users")
        result["users"] = _clone(users) if isinstance(users, dict) else {}

        raw_settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        settings = dict(raw_settings)
        settings.setdefault("public_panel", DEFAULT_SETTINGS["public_panel"])
        settings.setdefault(
            "notifications",
            raw_settings.get("wheel_notifications", DEFAULT_SETTINGS["notifications"]),
        )
        try:
            interval = int(raw_settings.get("monitor_interval_minutes", 5))
        except (TypeError, ValueError):
            interval = 5
        settings["monitor_interval_minutes"] = interval if interval in INTERVAL_OPTIONS else 5
        settings["public_panel"] = bool(settings["public_panel"])
        settings["notifications"] = bool(settings["notifications"])
        result["settings"] = settings

        owner_id = result["owner_id"]
        result["admins"] = [item for item in result["admins"] if item != owner_id]
        result["blocked_users"] = [
            item for item in result["blocked_users"] if item != owner_id
        ]
        result["version"] = max(4, int(raw.get("version", 4) or 4))
        result.pop("access_signature", None)
        return result

    def _normalize_bundle(self, value: dict[str, Any]) -> dict[str, Any]:
        access = value.get("access") if isinstance(value.get("access"), dict) else {}
        requests_value = (
            value.get("source_requests")
            if isinstance(value.get("source_requests"), dict)
            else default_source_requests()
        )
        return {
            "version": max(2, int(value.get("version", 2) or 2)),
            "access": self.normalize_access(self._bootstrap_access(access)),
            "source_requests": requests_value,
        }

    def _load_remote_bundle(self) -> tuple[dict[str, Any], str]:
        text, sha = self.get_file(bot_private_state.STATE_PATH.name)
        bundle = bot_private_state.load_text(
            text,
            access_default=self._bootstrap_access(),
            source_requests_default=default_source_requests(),
        )
        return self._normalize_bundle(bundle), sha

    def _load_bot_bundle(self, force: bool = False) -> dict[str, Any]:
        with self._bot_state_lock:
            if self._bot_bundle is not None and not force:
                return self._bot_bundle
            if force:
                bundle, _ = self._load_remote_bundle()
            else:
                bundle = bot_private_state.load_file(
                    access_default=self._bootstrap_access(),
                    source_requests_default=default_source_requests(),
                )
                bundle = self._normalize_bundle(bundle)
            self._bot_bundle = bundle
            self._bundle_baseline = _clone(bundle)
            return bundle

    def _merge_access(
        self,
        base: dict[str, Any],
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge access while preserving concurrent role and user changes."""

        base = self.normalize_access(base)
        local = self.normalize_access(local)
        remote = self.normalize_access(remote)
        result = _clone(remote)

        if local.get("owner_id") != base.get("owner_id"):
            result["owner_id"] = str(local.get("owner_id") or "")

        for key in ("admins", "blocked_users", "notification_recipients"):
            result[key] = _merge_set_list(base.get(key), local.get(key), remote.get(key))

        result["settings"] = _merge_value(
            base.get("settings", {}),
            local.get("settings", {}),
            remote.get("settings", {}),
        )

        base_users = base.get("users") if isinstance(base.get("users"), dict) else {}
        local_users = local.get("users") if isinstance(local.get("users"), dict) else {}
        remote_users = remote.get("users") if isinstance(remote.get("users"), dict) else {}
        merged_users = _clone(remote_users)

        for user_id in set(base_users) | set(local_users):
            base_record = (
                base_users.get(user_id) if isinstance(base_users.get(user_id), dict) else {}
            )
            if user_id not in local_users:
                if user_id in base_users:
                    merged_users.pop(user_id, None)
                continue
            local_record = (
                local_users.get(user_id) if isinstance(local_users.get(user_id), dict) else {}
            )
            if user_id in base_users and user_id not in remote_users and local_record == base_record:
                # A fresher process explicitly removed the user; stale unchanged
                # local state must not recreate it.
                merged_users.pop(user_id, None)
                continue
            remote_record = (
                remote_users.get(user_id)
                if isinstance(remote_users.get(user_id), dict)
                else {}
            )
            merged_users[user_id] = _merge_value(base_record, local_record, remote_record)

        result["users"] = merged_users
        return self.normalize_access(result)

    def _write_remote_bundle(self, bundle: dict[str, Any], sha: str, message: str) -> str:
        privacy_retention.prune_bundle(bundle)
        text = bot_private_state.seal(bundle)
        body = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": legacy.GITHUB_BRANCH,
        }
        self.gh_request(
            "PUT",
            (
                f"/repos/{legacy.GITHUB_REPOSITORY}/contents/"
                f"{quote(bot_private_state.STATE_PATH.name, safe='/')}"
            ),
            json_body=body,
            expected=(200, 201),
        )
        bot_private_state.STATE_PATH.write_text(text, encoding="utf-8")
        return text

    def _save_bot_bundle(self, message: str) -> bool:
        """Persist a three-way merge so stale processes cannot erase roles."""

        with self._bot_state_lock:
            local = self._normalize_bundle(self._load_bot_bundle())
            base = self._normalize_bundle(self._bundle_baseline or local)
            last_error: Exception | None = None
            for _attempt in range(3):
                try:
                    remote, sha = self._load_remote_bundle()
                    merged = {
                        "version": max(
                            int(base.get("version", 2) or 2),
                            int(local.get("version", 2) or 2),
                            int(remote.get("version", 2) or 2),
                        ),
                        "access": self._merge_access(
                            base.get("access", {}),
                            local.get("access", {}),
                            remote.get("access", {}),
                        ),
                        "source_requests": _merge_value(
                            base.get("source_requests", {}),
                            local.get("source_requests", {}),
                            remote.get("source_requests", {}),
                        ),
                    }
                    self._write_remote_bundle(merged, sha, message)
                    self._bot_bundle = merged
                    self._bundle_baseline = _clone(merged)
                    with self.access_lock:
                        self.access = self.normalize_access(merged["access"])
                        self.access_loaded = True
                    return True
                except Exception as exc:
                    last_error = exc
            raise RuntimeError(
                "Не удалось безопасно сохранить состояние без потери ролей"
            ) from last_error

    def load_access(self, force: bool = False) -> dict[str, Any]:
        with self.access_lock:
            if self.access_loaded and not force:
                return self.access
            bundle = self._load_bot_bundle(force=force)
            self.access = self.normalize_access(bundle["access"])
            self.access_loaded = True
            return self.access

    def save_access(self, message: str = "Update Telegram bot access [skip ci]") -> None:
        with self.access_lock:
            normalized = self.normalize_access(self.access)
            bundle = self._load_bot_bundle()
            bundle["access"] = normalized
            self.access = normalized
            self.access_loaded = True
            self._save_bot_bundle(message)

    def load_source_requests(self) -> dict[str, Any]:
        value = self._load_bot_bundle().get("source_requests")
        requests_value = value.get("requests") if isinstance(value, dict) else None
        return {
            "version": 1,
            "requests": requests_value if isinstance(requests_value, dict) else {},
        }

    def save_source_requests(self, value: dict[str, Any], message: str) -> None:
        bundle = self._load_bot_bundle()
        requests_value = value.get("requests") if isinstance(value, dict) else None
        bundle["source_requests"] = {
            "version": 1,
            "requests": requests_value if isinstance(requests_value, dict) else {},
        }
        self._save_bot_bundle(message)


def self_test() -> None:
    bot_private_state.self_test()

    panel = PrivateStateRuntime()
    base = panel.normalize_access(
        {
            "owner_id": "1",
            "admins": ["2"],
            "blocked_users": [],
            "notification_recipients": ["10"],
            "settings": {"public_panel": True, "notifications": True},
            "users": {
                "1": {"id": "1", "chat_id": "10", "first_name": "Owner"},
                "2": {
                    "id": "2",
                    "chat_id": "20",
                    "first_name": "User",
                    "notification_preferences": {"wheels": True},
                },
            },
            "access_signature": "obsolete",
        }
    )
    assert "access_signature" not in base

    local = _clone(base)
    local["users"]["2"]["notification_preferences"]["wheel_final_reminders"] = False
    remote = _clone(base)
    remote["admins"].append("3")
    remote["users"]["2"]["last_name"] = "Remote"
    remote["users"]["3"] = {"id": "3", "chat_id": "30"}
    merged = panel._merge_access(base, local, remote)
    assert merged["admins"] == ["2", "3"]
    assert merged["users"]["2"]["last_name"] == "Remote"
    assert merged["users"]["2"]["notification_preferences"]["wheel_final_reminders"] is False
    assert "3" in merged["users"]

    unchanged_local = _clone(base)
    remote_deleted = _clone(base)
    remote_deleted["users"].pop("2")
    merged_deleted = panel._merge_access(base, unchanged_local, remote_deleted)
    assert "2" not in merged_deleted["users"]

    local_role = _clone(base)
    local_role["admins"] = []
    remote_role = _clone(base)
    remote_role["admins"] = ["2", "3"]
    merged_role = panel._merge_access(base, local_role, remote_role)
    assert merged_role["admins"] == ["3"]

    bundle = panel._normalize_bundle(
        {"access": merged, "source_requests": default_source_requests()}
    )
    sealed = bot_private_state.seal(bundle, secret="storage-self-test-key")
    round_trip = bot_private_state.load_text(
        sealed,
        access_default=panel._bootstrap_access(),
        source_requests_default=default_source_requests(),
        secret="storage-self-test-key",
    )
    assert round_trip["access"]["owner_id"] == "1"
    assert round_trip["source_requests"]["version"] == 1
    print("BB V.G. encrypted private state subsystem self-test passed")


if __name__ == "__main__":
    self_test()
