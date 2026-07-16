from __future__ import annotations

import argparse
import html
from typing import Any

from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS
from admin_panel_runtime_v33 import (
    SUMMARY_NOTIFICATION_OPTIONS,
    WHEEL_NOTIFICATION_OPTIONS,
    TelegramPanelRuntimeV33,
)
from bbvg.bot.storage import (
    _clone,
    _merge_set_list,
    _merge_value,
    self_test as storage_self_test,
)

ALL_SUMMARY_NOTIFICATION_OPTIONS = tuple(SUMMARY_NOTIFICATION_OPTIONS)


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
    """Owner-managed notification interface over consolidated private storage."""

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
                [{
                    "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                    "callback_data": f"notify:{key}",
                }]
            )
        if admin:
            lines.extend(["", "<b>Сводки</b>"])
            for key, label, description in ALL_SUMMARY_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(bool(prefs.get(key, False)))} "
                    f"{html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [{
                        "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                        "callback_data": f"notify:{key}",
                    }]
                )
            lines.extend(["", "<b>Административные</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(bool(prefs.get(key, False)))} "
                    f"{html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [{
                        "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                        "callback_data": f"notify:{key}",
                    }]
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
            [{
                "text": "🔔 Управлять уведомлениями",
                "callback_data": f"usernotifications:{user_id}",
            }]
        ]
        if role == "user":
            rows.append(
                [{
                    "text": "Сделать администратором",
                    "callback_data": f"access:promote:{user_id}",
                }]
            )
        elif role == "admin":
            rows.append(
                [{
                    "text": "Убрать права администратора",
                    "callback_data": f"access:demote:{user_id}",
                }]
            )
        if role != "owner":
            rows.append(
                [{
                    "text": "👑 Передать владение",
                    "callback_data": f"access:transferask:{user_id}",
                }]
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
                [{
                    "text": f"{self.bool_mark(bool(prefs.get(key, False)))} {label}",
                    "callback_data": f"usernotify:{user_id}:{key}",
                }]
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
                [{"text": "👤 К пользователю", "callback_data": f"page:user:{user_id}"}],
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
    storage_self_test()
    panel = TelegramPanelRuntimeV34()
    access = {
        "owner_id": "1",
        "admins": [],
        "blocked_users": [],
        "notification_recipients": ["10", "20"],
        "settings": {},
        "users": {
            "1": {"id": "1", "chat_id": "10"},
            "2": {
                "id": "2",
                "chat_id": "20",
                "notification_preferences": {"wheels": True},
            },
        },
    }
    saved: list[str] = []
    panel.current_user_id = "1"
    panel.current_role = "owner"
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.load_access = lambda force=False: access  # type: ignore[method-assign]
    panel.save_access = lambda message="": saved.append(message)  # type: ignore[method-assign]
    panel.role_for = lambda user_id: "owner" if str(user_id) == "1" else "user"  # type: ignore[method-assign]
    panel.notification_preferences = lambda user_id=None: {  # type: ignore[method-assign]
        "wheels": bool(
            access["users"].get(str(user_id or panel.current_user_id), {})
            .get("notification_preferences", {})
            .get("wheels", True)
        ),
        "wheel_final_reminders": True,
        "wheel_draw_alerts": False,
        "daily_reports": False,
        "weekly_reports": False,
        "admin_system": False,
        "admin_sources": False,
        "admin_requests": False,
    }
    panel.set_user_notification("2", "wheels", False)
    assert access["users"]["2"]["notifications_enabled"] is False
    assert "20" not in access["notification_recipients"]
    assert saved
    print("admin panel v34 notification interface self-test passed")


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
