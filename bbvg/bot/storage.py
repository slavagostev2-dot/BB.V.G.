from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import requests

import admin_bot as legacy
import bot_notification_state
import bot_private_state
import source_intelligence_alerts
from bbvg.bot.source_requests import SOURCE_REQUESTS_PATH, default_source_requests
from bbvg.bot.sources import SourceRegistryRuntime

ROOT = Path(__file__).resolve().parents[2]
ACCESS_PATH = "bot_access.json"


class PrivateStateRuntime(SourceRegistryRuntime):
    """Encrypted private bundle with remote merge and role preservation."""

    def __init__(self) -> None:
        super().__init__()
        self._private_bundle: dict[str, Any] | None = None
        self._access_base: dict[str, Any] | None = None
        self._request_base: dict[str, Any] | None = None

    def get_file_or_none(self, path: str) -> tuple[str | None, str | None]:
        url = (
            f"https://api.github.com/repos/{legacy.REPOSITORY}/contents/{path}"
            f"?ref={legacy.BRANCH}"
        )
        response = self.http.get(
            url,
            headers=self.github_headers(),
            timeout=legacy.REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        payload = response.json()
        content = base64.b64decode(payload.get("content", "")).decode("utf-8")
        return content, str(payload.get("sha") or "") or None

    @staticmethod
    def decode_from_git(path: str, default: dict[str, Any]) -> dict[str, Any]:
        file_path = ROOT / path
        try:
            value = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(default)
        return value if isinstance(value, dict) else dict(default)

    def _refresh_roles(self) -> None:
        access = self.access_cache if isinstance(self.access_cache, dict) else {}
        owner = str(access.get("owner_id") or "")
        admins = {str(value) for value in access.get("admins", [])}
        blocked = {str(value) for value in access.get("blocked_users", [])}
        if self.current_user_id:
            self.current_role = (
                "owner"
                if self.current_user_id == owner
                else (
                    "admin"
                    if self.current_user_id in admins
                    else (
                        "blocked"
                        if self.current_user_id in blocked
                        else self.role_for(self.current_user_id)
                    )
                )
            )

    @staticmethod
    def _merge_user_record(
        base: dict[str, Any],
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> dict[str, Any]:
        merged = bot_private_state.merge_three_way(base, local, remote)
        for key in (
            "notification_preferences",
            "participating_wheels",
            "hidden_wheels",
        ):
            merged[key] = bot_private_state.merge_three_way(
                base.get(key) if isinstance(base.get(key), dict) else {},
                local.get(key) if isinstance(local.get(key), dict) else {},
                remote.get(key) if isinstance(remote.get(key), dict) else {},
            )
        return merged

    @classmethod
    def _merge_access(
        cls,
        base: dict[str, Any],
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> dict[str, Any]:
        merged = bot_private_state.merge_three_way(base, local, remote)
        base_users = base.get("users") if isinstance(base.get("users"), dict) else {}
        local_users = local.get("users") if isinstance(local.get("users"), dict) else {}
        remote_users = (
            remote.get("users") if isinstance(remote.get("users"), dict) else {}
        )
        users: dict[str, Any] = {}
        for user_id in sorted({*base_users, *local_users, *remote_users}):
            users[str(user_id)] = cls._merge_user_record(
                base_users.get(user_id)
                if isinstance(base_users.get(user_id), dict)
                else {},
                local_users.get(user_id)
                if isinstance(local_users.get(user_id), dict)
                else {},
                remote_users.get(user_id)
                if isinstance(remote_users.get(user_id), dict)
                else {},
            )
        merged["users"] = users
        merged["admins"] = sorted(
            {
                str(value)
                for source in (local.get("admins", []), remote.get("admins", []))
                for value in (source if isinstance(source, list) else [])
                if str(value)
            }
        )
        merged["blocked_users"] = sorted(
            {
                str(value)
                for source in (
                    local.get("blocked_users", []),
                    remote.get("blocked_users", []),
                )
                for value in (source if isinstance(source, list) else [])
                if str(value)
            }
        )
        owner = str(local.get("owner_id") or remote.get("owner_id") or "")
        if owner:
            merged["owner_id"] = owner
            merged["admins"] = [
                value for value in merged["admins"] if value != owner
            ]
            merged["blocked_users"] = [
                value for value in merged["blocked_users"] if value != owner
            ]
        merged["notification_recipients"] = sorted(
            {
                str(value)
                for source in (
                    local.get("notification_recipients", []),
                    remote.get("notification_recipients", []),
                )
                for value in (source if isinstance(source, list) else [])
                if str(value)
            }
        )
        return merged

    def _load_remote_bundle(self, *, force: bool = False) -> dict[str, Any]:
        if self._private_bundle is not None and not force:
            return self._private_bundle
        prior_access = self.access_cache if isinstance(self.access_cache, dict) else None
        try:
            content, sha = self.get_file_or_none(bot_private_state.ENCRYPTED_PATH.name)
            if content:
                payload = json.loads(content)
                value = bot_private_state.decrypt_payload(payload)
                if prior_access is not None:
                    remote_access = value.get("access")
                    value["access"] = self._merge_access(
                        {},
                        prior_access,
                        remote_access if isinstance(remote_access, dict) else {},
                    )
                self.file_shas[bot_private_state.ENCRYPTED_PATH.name] = sha
            else:
                value = bot_private_state.load_file()
        except Exception as exc:
            print(f"WARNING remote private state: {type(exc).__name__}: {exc}")
            value = bot_private_state.load_file()
        if not isinstance(value, dict):
            value = bot_private_state.default_private_state()
        self._private_bundle = value
        access = value.get("access")
        requests_value = value.get("source_requests")
        self._access_base = (
            json.loads(json.dumps(access)) if isinstance(access, dict) else None
        )
        self._request_base = (
            json.loads(json.dumps(requests_value))
            if isinstance(requests_value, dict)
            else None
        )
        return value

    @staticmethod
    def _role_snapshot(value: dict[str, Any] | None) -> tuple[str, set[str], set[str]]:
        source = value if isinstance(value, dict) else {}
        return (
            str(source.get("owner_id") or ""),
            {str(item) for item in source.get("admins", []) if str(item)},
            {str(item) for item in source.get("blocked_users", []) if str(item)},
        )

    @classmethod
    def _roles_missing(
        cls,
        candidate: dict[str, Any] | None,
        preserved: dict[str, Any] | None,
    ) -> bool:
        preserved_owner, preserved_admins, preserved_blocked = cls._role_snapshot(
            preserved
        )
        candidate_owner, candidate_admins, candidate_blocked = cls._role_snapshot(
            candidate
        )
        return bool(
            (preserved_owner and candidate_owner != preserved_owner)
            or not preserved_admins.issubset(candidate_admins)
            or not preserved_blocked.issubset(candidate_blocked)
        )

    @classmethod
    def _merge_preserved_roles(
        cls,
        candidate: dict[str, Any],
        preserved: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = json.loads(json.dumps(candidate))
        owner, admins, blocked = cls._role_snapshot(preserved)
        if owner:
            result["owner_id"] = owner
        if admins:
            result["admins"] = sorted(
                {str(value) for value in result.get("admins", []) if str(value)}
                | admins
            )
        if blocked:
            result["blocked_users"] = sorted(
                {
                    str(value)
                    for value in result.get("blocked_users", [])
                    if str(value)
                }
                | blocked
            )
        current_owner = str(result.get("owner_id") or "")
        if current_owner:
            result["admins"] = [
                value for value in result.get("admins", []) if value != current_owner
            ]
            result["blocked_users"] = [
                value
                for value in result.get("blocked_users", [])
                if value != current_owner
            ]
        return result

    def load_access(self, force: bool = False) -> dict[str, Any]:
        prior = self.access_cache if isinstance(self.access_cache, dict) else None
        bundle = self._load_remote_bundle(force=force)
        raw = bundle.get("access")
        value = raw if isinstance(raw, dict) else self.decode_from_git(
            ACCESS_PATH,
            self.default_access(),
        )
        if prior is not None:
            value = self._merge_access({}, prior, value)
        self.access_cache = self.normalize_access(value)
        self._refresh_roles()
        return self.access_cache

    def _save_bot_bundle(
        self,
        *,
        access: dict[str, Any] | None = None,
        source_requests: dict[str, Any] | None = None,
        message: str,
    ) -> None:
        local_access = access if isinstance(access, dict) else self.load_access()
        local_requests = (
            source_requests
            if isinstance(source_requests, dict)
            else self.load_source_requests()
        )
        self._private_bundle = None
        remote_bundle = self._load_remote_bundle(force=True)
        remote_access = (
            remote_bundle.get("access")
            if isinstance(remote_bundle.get("access"), dict)
            else {}
        )
        remote_requests = (
            remote_bundle.get("source_requests")
            if isinstance(remote_bundle.get("source_requests"), dict)
            else {}
        )
        merged_access = self._merge_access(
            self._access_base or {},
            local_access,
            remote_access,
        )
        merged_requests = bot_private_state.merge_three_way(
            self._request_base or {},
            local_requests,
            remote_requests,
        )
        notifications = remote_bundle.get("notifications")
        if not isinstance(notifications, dict):
            notifications = bot_notification_state.default_state()
        value = {
            "version": 2,
            "access": merged_access,
            "source_requests": merged_requests,
            "notifications": notifications,
        }
        bot_private_state.save_file(value)
        encrypted = bot_private_state.ENCRYPTED_PATH.read_text(encoding="utf-8")
        self.update_file(bot_private_state.ENCRYPTED_PATH.name, encrypted, message)
        self.access_cache = self.normalize_access(merged_access)
        self._private_bundle = value
        self._access_base = json.loads(json.dumps(merged_access))
        self._request_base = json.loads(json.dumps(merged_requests))
        self._refresh_roles()

    def save_access(self, message: str) -> None:
        local = self.normalize_access(self.access_cache or self.default_access())
        try:
            encrypted_content, _ = self.get_file_or_none(
                bot_private_state.ENCRYPTED_PATH.name
            )
            if encrypted_content:
                remote_bundle = bot_private_state.decrypt_payload(
                    json.loads(encrypted_content)
                )
                remote = (
                    remote_bundle.get("access")
                    if isinstance(remote_bundle.get("access"), dict)
                    else {}
                )
                local = self._merge_preserved_roles(local, remote)
        except Exception as exc:
            print(f"WARNING preserve remote roles: {type(exc).__name__}: {exc}")

        self.access_cache = self.normalize_access(local)
        self._save_bot_bundle(access=self.access_cache, message=message)
        self._private_bundle = None
        self.load_access(force=True)

    def load_source_requests(self) -> dict[str, Any]:
        bundle = self._load_remote_bundle()
        raw = bundle.get("source_requests")
        value = raw if isinstance(raw, dict) else self.decode_from_git(
            SOURCE_REQUESTS_PATH,
            default_source_requests(),
        )
        requests_value = value.get("requests")
        value["requests"] = requests_value if isinstance(requests_value, dict) else {}
        value["version"] = 1
        self._private_bundle["source_requests"] = value
        return value

    def save_source_requests(self, value: dict[str, Any], message: str) -> None:
        self._save_bot_bundle(source_requests=value, message=message)

    def notification_recipients(self, category: str = "wheels") -> list[str]:
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        result = []
        for user_id, record in users.items():
            if not isinstance(record, dict):
                continue
            role = self.role_for(str(user_id))
            prefs = self.notification_preferences(str(user_id))
            if category.startswith("admin_") and role not in {"owner", "admin"}:
                continue
            if prefs.get(category):
                result.append(str(record.get("chat_id") or user_id))
        return sorted({value for value in result if value})


def self_test() -> None:
    base = {
        "owner_id": "1",
        "admins": ["2"],
        "blocked_users": [],
        "users": {
            "1": {"id": "1", "notification_preferences": {"wheels": True}},
            "2": {"id": "2"},
        },
    }
    local = json.loads(json.dumps(base))
    remote = json.loads(json.dumps(base))
    local["users"]["1"]["first_name"] = "Local"
    remote["users"]["2"]["username"] = "remote_admin"
    remote["admins"].append("3")
    merged = PrivateStateRuntime._merge_access(base, local, remote)
    assert merged["users"]["1"]["first_name"] == "Local"
    assert merged["users"]["2"]["username"] == "remote_admin"
    assert merged["admins"] == ["2", "3"]

    candidate = {"owner_id": "", "admins": [], "blocked_users": []}
    preserved = {"owner_id": "1", "admins": ["2"], "blocked_users": ["9"]}
    assert PrivateStateRuntime._roles_missing(candidate, preserved) is True
    restored = PrivateStateRuntime._merge_preserved_roles(candidate, preserved)
    assert restored["owner_id"] == "1"
    assert restored["admins"] == ["2"]
    assert restored["blocked_users"] == ["9"]

    assert ACCESS_PATH == "bot_access.json"
    assert SOURCE_REQUESTS_PATH == "source_requests.json"
    print("BB V.G. encrypted private state subsystem self-test passed")


if __name__ == "__main__":
    self_test()
