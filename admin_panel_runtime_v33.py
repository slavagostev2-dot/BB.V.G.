from __future__ import annotations

import argparse
import html
from typing import Any

import bot_private_state
import privacy_retention
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS, USER_NOTIFICATION_OPTIONS
from admin_panel_runtime_v32 import TelegramPanelRuntimeV32

WHEEL_NOTIFICATION_OPTIONS = (
    ("wheels", "🎡 Новые колёса", "Новые и подтверждённые колёса"),
    (
        "wheel_final_reminders",
        "⏳ Перед завершением",
        "Последнее напоминание незадолго до завершения колеса",
    ),
    (
        "wheel_draw_alerts",
        "🎯 Время прокрутки",
        "Сообщение, когда наступило указанное время прокрутки",
    ),
)
SUMMARY_NOTIFICATION_OPTIONS = USER_NOTIFICATION_OPTIONS[1:]


class TelegramPanelRuntimeV33(TelegramPanelRuntimeV32):
    """Security chapter 1: granular reminders and personal-data controls."""

    def notification_preferences(self, user_id: str | None = None) -> dict[str, bool]:
        prefs = super().notification_preferences(user_id)
        target = str(user_id or self.current_user_id or "")
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(target) if isinstance(users.get(target), dict) else {}
        raw = record.get("notification_preferences") if isinstance(record, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        wheel_default = bool(prefs.get("wheels", True))
        prefs["wheel_final_reminders"] = bool(
            raw.get("wheel_final_reminders", wheel_default)
        )
        prefs["wheel_draw_alerts"] = bool(raw.get("wheel_draw_alerts", wheel_default))
        return prefs

    def register_user(self, message: dict[str, Any]) -> str:
        role = super().register_user(message)
        user_id = str((message.get("from") or {}).get("id") or "")
        if not user_id:
            return role
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(user_id) if isinstance(users.get(user_id), dict) else None
        if not isinstance(record, dict):
            return role
        prefs = record.get("notification_preferences")
        prefs = dict(prefs) if isinstance(prefs, dict) else {}
        changed = False
        for key in ("wheel_final_reminders", "wheel_draw_alerts"):
            if key not in prefs:
                prefs[key] = True
                changed = True
        if changed:
            record["notification_preferences"] = prefs
            self.save_access(
                f"Enable wheel reminder notifications for Telegram user {user_id} [skip ci]"
            )
        return role

    def show_settings(self) -> None:
        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
        ]
        lines = [
            "⚙️ <b>Настройки</b>",
            "",
            "Настройки применяются лично к вашему Telegram-аккаунту.",
        ]
        if self.is_admin():
            interval = int(
                self.load_access().get("settings", {}).get("monitor_interval_minutes", 5)
            )
            lines.extend(["", f"Интервал постоянной проверки: <b>{interval} мин.</b>"])
            rows.append([{"text": "⏱ Интервал проверки", "callback_data": "page:interval"}])
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
            lines.extend(
                [
                    "",
                    "Удаление данных владельца возможно только после передачи владения другому пользователю.",
                ]
            )
        else:
            rows.append(
                [{"text": "🗑 Удалить мои данные", "callback_data": "privacy:delete:ask"}]
            )
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

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
                f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}"
            )
            rows.append(
                [{"text": f"{self.bool_mark(prefs[key])} {label}", "callback_data": f"notify:{key}"}]
            )
        if admin:
            lines.extend(["", "<b>Сводки</b>"])
            for key, label, description in SUMMARY_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [{"text": f"{self.bool_mark(prefs[key])} {label}", "callback_data": f"notify:{key}"}]
                )
            lines.extend(["", "<b>Административные</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [{"text": f"{self.bool_mark(prefs[key])} {label}", "callback_data": f"notify:{key}"}]
                )
        else:
            lines.extend(["", "Сводки и служебные уведомления доступны только администраторам."])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def toggle_notification(self, key: str) -> None:
        personal_allowed = {name for name, _, _ in WHEEL_NOTIFICATION_OPTIONS}
        admin_allowed = {
            name for name, _, _ in (*SUMMARY_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS)
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
        prefs[key] = not prefs[key]
        if not self.is_admin():
            for admin_key in admin_allowed:
                prefs[admin_key] = False
        record["notification_preferences"] = prefs
        record["notifications_enabled"] = prefs["wheels"]

        chat_id = str(record.get("chat_id") or self.current_user_id)
        recipients = {str(value) for value in access.get("notification_recipients", [])}
        if prefs["wheels"]:
            recipients.add(chat_id)
        else:
            recipients.discard(chat_id)
        access["notification_recipients"] = sorted(recipients)
        self.save_access(
            f"Update personal notification preferences for {self.current_user_id} [skip ci]"
        )
        self.dispatch("monitor.yml", {"continuous": "true"})

    def delete_current_user_data(self) -> bool:
        if not self.current_user_id:
            raise PermissionError("Пользователь не определён")
        if self.is_owner():
            raise PermissionError("Сначала передайте владение другому пользователю")
        bundle = self._load_bot_bundle(force=True)
        changed = privacy_retention.delete_user_data(bundle, str(self.current_user_id))
        if changed:
            self._save_bot_bundle(
                f"Delete Telegram user personal data {self.current_user_id} [skip ci]"
            )
        with self.access_lock:
            self.access = {}
            self.access_loaded = False
        return changed

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data.startswith("privacy:delete:"):
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            action = data.rsplit(":", 1)[-1]
            if action == "ask":
                if self.is_owner():
                    self.answer(query_id, "Сначала передайте владение")
                    self.show_settings()
                    return
                self.answer(query_id, "Нужно подтверждение")
                self.send(
                    "🗑 <b>Удалить мои данные?</b>\n\n"
                    "Будут удалены профиль, настройки, личные отметки участия и ожидающие заявки. "
                    "Обработанные заявки останутся только в обезличенном виде.\n\n"
                    "После удаления можно снова зарегистрироваться командой /start.",
                    reply_markup=self.with_nav(
                        [
                            [
                                {"text": "Удалить", "callback_data": "privacy:delete:confirm"},
                                {"text": "Отмена", "callback_data": "privacy:delete:cancel"},
                            ]
                        ]
                    ),
                )
                return
            if action == "cancel":
                self.answer(query_id, "Отменено")
                self.show_settings()
                return
            if action == "confirm":
                try:
                    changed = self.delete_current_user_data()
                except PermissionError as exc:
                    self.answer(query_id, "Недоступно")
                    self.send(html.escape(str(exc)), reply_markup=self.with_nav())
                    return
                self.answer(query_id, "Данные удалены" if changed else "Данные уже удалены")
                self.send(
                    "✅ <b>Ваши данные удалены.</b>\n\n"
                    "Уведомления отключены. Для повторной регистрации отправьте /start."
                )
                return
        super().handle_callback(query)


def self_test() -> None:
    assert {key for key, _, _ in WHEEL_NOTIFICATION_OPTIONS} == {
        "wheels",
        "wheel_final_reminders",
        "wheel_draw_alerts",
    }
    panel = TelegramPanelRuntimeV33()
    access = panel._bootstrap_access(
        {
            "owner_id": "1",
            "users": {
                "1": {"id": "1", "chat_id": "1", "first_name": "Owner"},
                "2": {"id": "2", "chat_id": "2", "first_name": "User"},
            },
        }
    )
    panel._bot_bundle = bot_private_state.default_bundle(access, default_source_requests())
    panel.load_access(force=True)
    panel.current_user_id = "2"
    panel.current_chat_id = "2"
    panel.current_role = "user"
    prefs = panel.notification_preferences("2")
    assert prefs["wheel_final_reminders"] is True
    assert prefs["wheel_draw_alerts"] is True
    saved: list[str] = []
    panel._save_bot_bundle = lambda message: saved.append(message) or True  # type: ignore[method-assign]
    assert panel.delete_current_user_data()
    assert "2" not in panel._bot_bundle["access"]["users"]
    assert saved
    print("admin panel v33 security and reminder preferences self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV33().run()


if __name__ == "__main__":
    raise SystemExit(main())
