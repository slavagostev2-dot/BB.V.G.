from __future__ import annotations

import argparse
import base64
import copy
import html
import json
from typing import Any
from urllib.parse import quote

import admin_bot as legacy
import bot_private_state
import privacy_retention
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS
from admin_panel_runtime_v33 import (
    SUMMARY_NOTIFICATION_OPTIONS,
    WHEEL_NOTIFICATION_OPTIONS,
    TelegramPanelRuntimeV33,
)

ALL_SUMMARY_NOTIFICATION_OPTIONS = tuple(SUMMARY_NOTIFICATION_OPTIONS)
_MISSING = object()


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _merge_value(base: Any, local: Any, remote: Any) -> Any:
    """Three-way merge: apply local changes to the freshest remote value."""

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


def _display_name(record: dict[str, Any], user_id: str) -> str:
    full_name = " ".join(
        value
        for value in (
            str(record.get("first_name") or "").strip(),
            str(record.get("last_name") or "").strip(),
        )
        if value
    )
    return full_name or str(record.get("username") or user_id)


class TelegramPanelRuntimeV34(TelegramPanelRuntimeV33):
    """Persistent roles and owner-managed notification preferences."""

    def __init__(self) -> None:
        super().__init__()
        self._bundle_baseline: dict[str, Any] | None = None

    def _normalize_bundle(self, value: dict[str, Any]) -> dict[str, Any]:
        access = value.get("access") if isinstance(value.get("access"), dict) else {}
        requests = (
            value.get("source_requests")
            if isinstance(value.get("source_requests"), dict)
            else default_source_requests()
        )
        return {
            "version": max(2, int(value.get("version", 2) or 2)),
            "access": self._bootstrap_access(access),
            "source_requests": requests,
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
                bundle = self._normalize_bundle(super()._load_bot_bundle(force=False))
            self._bot_bundle = bundle
            self._bundle_baseline = _clone(bundle)
            return bundle

    def _merge_access(
        self,
        base: dict[str, Any],
        local: dict[str, Any],
        remote: dict[str, Any],
    ) -> dict[str, Any]:
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
            if user_id not in local_users:
                if user_id in base_users:
                    merged_users.pop(user_id, None)
                continue
            local_record = (
                local_users.get(user_id) if isinstance(local_users.get(user_id), dict) else {}
            )
            if user_id not in base_users:
                remote_record = (
                    remote_users.get(user_id)
                    if isinstance(remote_users.get(user_id), dict)
                    else {}
                )
                merged_users[user_id] = _merge_value({}, local_record, remote_record)
                continue
            base_record = (
                base_users.get(user_id) if isinstance(base_users.get(user_id), dict) else {}
            )
            remote_record = (
                remote_users.get(user_id)
                if isinstance(remote_users.get(user_id), dict)
                else {}
            )
            merged_users[user_id] = _merge_value(base_record, local_record, remote_record)

        result["users"] = merged_users
        owner_id = str(result.get("owner_id") or "")
        result["admins"] = sorted(
            {
                str(value)
                for value in result.get("admins", [])
                if str(value) and str(value) != owner_id
            }
        )
        # The result is produced from three already trusted snapshots.  Refresh
        # the legacy v34 role signature before its final normalization; without
        # this, a valid local role change was cleared as if it were tampering.
        result["access_signature"] = self._signature(result)
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

    def show_notifications(self) -> None:
        prefs = self.notification_preferences()
        admin = self.is_admin()
        lines = [
            "🔔 <b>Уведомления</b>",
            "",
            "Каждый вид можно включать и отключать отдельно.",
            "",
            "<b>Колёса</b>",
        ]
        rows: list[list[dict[str, Any]]] = []
        for key, label, description in WHEEL_NOTIFICATION_OPTIONS:
            lines.append(
                f"{self.bool_mark(bool(prefs.get(key, False)))} "
                f"{html.escape(label)} — {html.escape(description)}"
            )
            rows.append(
                [
                    {
                        "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                        "callback_data": f"notify:{key}",
                    }
                ]
            )
        if admin:
            lines.extend(["", "<b>Сводки</b>"])
            for key, label, description in ALL_SUMMARY_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(bool(prefs.get(key, False)))} "
                    f"{html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [
                        {
                            "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                            "callback_data": f"notify:{key}",
                        }
                    ]
                )
            lines.extend(["", "<b>Административные</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(bool(prefs.get(key, False)))} "
                    f"{html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [
                        {
                            "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                            "callback_data": f"notify:{key}",
                        }
                    ]
                )
        else:
            lines.extend(
                ["", "Сводки и служебные уведомления доступны только администраторам."]
            )
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def toggle_notification(self, key: str) -> None:
        personal_allowed = {name for name, _, _ in WHEEL_NOTIFICATION_OPTIONS}
        admin_allowed = {
            name
            for name, _, _ in (*ALL_SUMMARY_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS)
        }
        allowed = personal_allowed | (admin_allowed if self.is_admin() else set())
        if key not in allowed or not self.current_user_id:
            raise PermissionError("Недоступный вид уведомлений")

        access = self.load_access()
        users = access.setdefault("users", {})
        record = users.get(str(self.current_user_id))
        if not isinstance(record, dict):
            record = {
                "id": str(self.current_user_id),
                "chat_id": str(self.current_chat_id or self.current_user_id),
            }
            users[str(self.current_user_id)] = record
        prefs = self.notification_preferences(str(self.current_user_id))
        prefs[key] = not bool(prefs.get(key, False))
        if not self.is_admin():
            for admin_key in admin_allowed:
                prefs[admin_key] = False
        record["notification_preferences"] = prefs
        record["notifications_enabled"] = bool(prefs.get("wheels", True))

        chat_id = str(record.get("chat_id") or self.current_user_id)
        recipients = {str(value) for value in access.get("notification_recipients", [])}
        if record["notifications_enabled"]:
            recipients.add(chat_id)
        else:
            recipients.discard(chat_id)
        access["notification_recipients"] = sorted(recipients)
        self.save_access(
            f"Update personal notification preferences for {self.current_user_id} [skip ci]"
        )
        self.dispatch("monitor.yml", {"continuous": "true"})

    @staticmethod
    def _notification_options_for_role(
        role: str,
    ) -> tuple[tuple[str, str, str], ...]:
        options: tuple[tuple[str, str, str], ...] = tuple(WHEEL_NOTIFICATION_OPTIONS)
        if role in {"owner", "admin"}:
            options += tuple(ALL_SUMMARY_NOTIFICATION_OPTIONS)
            options += tuple(ADMIN_NOTIFICATION_OPTIONS)
        return options

    def show_user_detail(self, user_id: str) -> None:
        if not self.is_owner():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        access = self.load_access(force=True)
        record = access.get("users", {}).get(user_id, {})
        if not isinstance(record, dict):
            self.send("Пользователь не найден.", reply_markup=self.with_nav())
            return
        role = self.role_for(user_id)
        prefs = self.notification_preferences(user_id)
        enabled_count = sum(
            1
            for key, _, _ in self._notification_options_for_role(role)
            if prefs.get(key, False)
        )
        total_count = len(self._notification_options_for_role(role))
        name = _display_name(record, user_id)
        text = (
            f"👤 <b>{html.escape(name)}</b>\n\n"
            f"Telegram ID: <code>{html.escape(user_id)}</code>\n"
            f"Роль: {self.role_name(role)}\n"
            f"Уведомления: <b>{enabled_count} из {total_count}</b>\n"
            f"Последняя активность: {self.fmt_dt(record.get('last_seen_at'))}"
        )
        rows: list[list[dict[str, str]]] = [
            [
                {
                    "text": "🔔 Управлять уведомлениями",
                    "callback_data": f"usernotifications:{user_id}",
                }
            ]
        ]
        if role == "user":
            rows.append(
                [
                    {
                        "text": "Сделать администратором",
                        "callback_data": f"access:promote:{user_id}",
                    }
                ]
            )
        elif role == "admin":
            rows.append(
                [
                    {
                        "text": "Убрать права администратора",
                        "callback_data": f"access:demote:{user_id}",
                    }
                ]
            )
        if role != "owner":
            rows.append(
                [
                    {
                        "text": "👑 Передать владение",
                        "callback_data": f"access:transferask:{user_id}",
                    }
                ]
            )
        self.send(text, reply_markup=self.with_nav(rows))

    def show_user_notifications(self, user_id: str) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет уведомлениями пользователей")
        access = self.load_access(force=True)
        record = access.get("users", {}).get(user_id)
        if not isinstance(record, dict):
            raise ValueError("Пользователь не найден")
        role = self.role_for(user_id)
        prefs = self.notification_preferences(user_id)
        name = _display_name(record, user_id)
        lines = [
            "🔔 <b>Уведомления пользователя</b>",
            "",
            f"Пользователь: <b>{html.escape(name)}</b>",
            f"Роль: {self.role_name(role)}",
            "",
            "Изменения применяются только к этому Telegram-аккаунту.",
        ]
        rows: list[list[dict[str, str]]] = []
        current_section = ""
        wheel_keys = {key for key, _, _ in WHEEL_NOTIFICATION_OPTIONS}
        summary_keys = {key for key, _, _ in ALL_SUMMARY_NOTIFICATION_OPTIONS}
        for key, label, description in self._notification_options_for_role(role):
            section = (
                "Колёса"
                if key in wheel_keys
                else "Сводки"
                if key in summary_keys
                else "Административные"
            )
            if section != current_section:
                current_section = section
                lines.extend(["", f"<b>{section}</b>"])
            lines.append(
                f"{self.bool_mark(bool(prefs.get(key, False)))} "
                f"{html.escape(label)} — {html.escape(description)}"
            )
            rows.append(
                [
                    {
                        "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                        "callback_data": f"usernotify:{user_id}:{key}",
                    }
                ]
            )
        rows.extend(
            [
                [
                    {
                        "text": "✅ Включить все",
                        "callback_data": f"usernotifyall:{user_id}:on",
                    },
                    {
                        "text": "⛔ Отключить все",
                        "callback_data": f"usernotifyall:{user_id}:off",
                    },
                ],
                [
                    {
                        "text": "👤 К пользователю",
                        "callback_data": f"page:user:{user_id}",
                    }
                ],
            ]
        )
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def set_user_notification(
        self,
        user_id: str,
        key: str,
        enabled: bool | None = None,
    ) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет уведомлениями пользователей")
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(user_id)
        if not isinstance(record, dict):
            raise ValueError("Пользователь не найден")
        role = self.role_for(user_id)
        allowed = {name for name, _, _ in self._notification_options_for_role(role)}
        if key not in allowed:
            raise PermissionError("Этот вид уведомлений недоступен для роли пользователя")
        prefs = self.notification_preferences(user_id)
        prefs[key] = (not bool(prefs.get(key, False))) if enabled is None else bool(enabled)
        if role not in {"owner", "admin"}:
            for admin_key, _, _ in (*ALL_SUMMARY_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS):
                prefs[admin_key] = False
        record["notification_preferences"] = prefs
        record["notifications_enabled"] = bool(prefs.get("wheels", True))
        chat_id = str(record.get("chat_id") or user_id)
        recipients = {str(value) for value in access.get("notification_recipients", [])}
        if record["notifications_enabled"]:
            recipients.add(chat_id)
        else:
            recipients.discard(chat_id)
        access["notification_recipients"] = sorted(recipients)
        self.save_access(
            f"Owner updated notification {key} for Telegram user {user_id} [skip ci]"
        )

    def set_all_user_notifications(self, user_id: str, enabled: bool) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет уведомлениями пользователей")
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(user_id)
        if not isinstance(record, dict):
            raise ValueError("Пользователь не найден")
        role = self.role_for(user_id)
        prefs = self.notification_preferences(user_id)
        for key, _, _ in self._notification_options_for_role(role):
            prefs[key] = bool(enabled)
        if role not in {"owner", "admin"}:
            for admin_key, _, _ in (*ALL_SUMMARY_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS):
                prefs[admin_key] = False
        record["notification_preferences"] = prefs
        record["notifications_enabled"] = bool(prefs.get("wheels", True))
        chat_id = str(record.get("chat_id") or user_id)
        recipients = {str(value) for value in access.get("notification_recipients", [])}
        if record["notifications_enabled"]:
            recipients.add(chat_id)
        else:
            recipients.discard(chat_id)
        access["notification_recipients"] = sorted(recipients)
        self.save_access(
            f"Owner {'enabled' if enabled else 'disabled'} all notifications "
            f"for Telegram user {user_id} [skip ci]"
        )

    def render_page(self, page: str) -> None:
        if page.startswith("user_notifications:"):
            self.show_user_notifications(page.split(":", 1)[1])
            return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if (
            data.startswith("usernotifications:")
            or data.startswith("usernotify:")
            or data.startswith("usernotifyall:")
        ):
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            try:
                if not self.is_owner():
                    raise PermissionError
                if data.startswith("usernotifications:"):
                    target = data.split(":", 1)[1]
                    self.answer(query_id, "Открываю настройки")
                    self.show_user_notifications(target)
                    return
                if data.startswith("usernotifyall:"):
                    _, target, state = data.split(":", 2)
                    self.set_all_user_notifications(target, state == "on")
                    self.answer(query_id, "Настройки сохранены")
                    self.show_user_notifications(target)
                    return
                _, target, key = data.split(":", 2)
                self.set_user_notification(target, key)
                self.answer(query_id, "Настройка изменена")
                self.show_user_notifications(target)
            except PermissionError:
                self.answer(query_id, "Недоступно")
            except Exception as exc:
                print(f"ERROR owner notification management: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось сохранить")
                self.send(
                    "⚠️ Не удалось безопасно сохранить настройки пользователя.",
                    reply_markup=self.with_nav(),
                )
            return
        super().handle_callback(query)


def self_test() -> None:
    panel = TelegramPanelRuntimeV34()
    base = panel.normalize_access(
        {
            "owner_id": "1",
            "admins": [],
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
        }
    )
    local = _clone(base)
    local["users"]["2"]["notification_preferences"]["wheel_final_reminders"] = False
    remote = _clone(base)
    remote["admins"] = ["2"]
    remote["users"]["2"]["last_name"] = "Remote"
    remote["access_signature"] = panel._signature(remote)
    merged = panel._merge_access(base, local, remote)
    assert merged["admins"] == ["2"], "A stale preference write erased a remote administrator role"
    assert merged["users"]["2"]["last_name"] == "Remote"
    assert merged["users"]["2"]["notification_preferences"]["wheel_final_reminders"] is False

    role_local = _clone(base)
    role_local["admins"] = ["2"]
    role_local["access_signature"] = panel._signature(role_local)
    remote_with_new_user = _clone(base)
    remote_with_new_user["users"]["3"] = {"id": "3", "chat_id": "30"}
    merged_role = panel._merge_access(base, role_local, remote_with_new_user)
    assert merged_role["admins"] == ["2"]
    assert "3" in merged_role["users"], "Role update erased a newly registered user"

    panel._bot_bundle = bot_private_state.default_bundle(base, default_source_requests())
    panel._bundle_baseline = _clone(panel._bot_bundle)
    panel.access = base
    panel.access_loaded = True
    panel.current_user_id = "1"
    panel.current_chat_id = "10"
    panel.current_role = "owner"
    saved: list[str] = []
    panel._save_bot_bundle = lambda message: saved.append(message) or True  # type: ignore[method-assign]
    panel.set_user_notification("2", "wheel_final_reminders", False)
    assert (
        panel.access["users"]["2"]["notification_preferences"]["wheel_final_reminders"]
        is False
    )
    assert saved

    options = {
        key
        for key, _, _ in panel._notification_options_for_role("admin")
    }
    assert {
        "wheels",
        "wheel_final_reminders",
        "wheel_draw_alerts",
        "daily_reports",
        "weekly_reports",
        "admin_system",
        "admin_sources",
        "admin_requests",
    } <= options
    print("admin panel v34 persistent roles and owner notification management self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV34().run()


if __name__ == "__main__":
    raise SystemExit(main())
