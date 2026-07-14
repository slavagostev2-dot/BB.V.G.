from __future__ import annotations

import argparse
import html
from typing import Any
from urllib.parse import quote

from admin_panel_runtime_v20 import TelegramPanelRuntimeV20

MINIAPP_RELEASE = "5.6.0"
MINIAPP_URL = "https://slavagostev2-betboom-monitor.pages.dev/"

USER_NOTIFICATION_OPTIONS = (
    ("wheels", "🎡 Колёса", "Новые и подтверждённые колёса"),
    ("daily_reports", "📊 Ежедневная сводка", "Один итоговый отчёт за день"),
    ("weekly_reports", "📅 Недельная сводка", "Один итоговый отчёт за неделю"),
)
ADMIN_NOTIFICATION_OPTIONS = (
    ("admin_system", "🛠 Сбои системы", "Транспорт, Bot API, монитор и Mini App"),
    ("admin_sources", "📡 Проблемы источников", "Недоступность и изменение страниц каналов"),
    ("admin_requests", "📨 Заявки на источники", "Новые запросы пользователей"),
)


class TelegramPanelRuntimeV21(TelegramPanelRuntimeV20):
    """Current BB V.G. panel with role-safe unified notifications."""

    def miniapp_url_for_chat(self) -> str:
        deployment = self.miniapp_deployment()
        deployed = str(deployment.get("url") or "").strip()
        base = (
            deployed
            if deployment.get("status") == "deployed" and deployed.startswith("https://")
            else MINIAPP_URL
        )
        params = [f"release={MINIAPP_RELEASE}"]
        username = self.bot_username()
        if username:
            params.append(f"bot={quote(username)}")
        separator = "&" if "?" in base else "?"
        return base + separator + "&".join(params)

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = TelegramPanelRuntimeV20.compact_menu_rows(admin)
        if admin:
            return rows
        result = [list(row) for row in rows]
        result.insert(-1, [{"text": "⚙️ Настройки", "callback_data": "page:settings"}])
        return result

    def notification_preferences(self, user_id: str | None = None) -> dict[str, bool]:
        access = self.load_access()
        target = str(user_id or self.current_user_id or "")
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(target) if isinstance(users.get(target), dict) else {}
        role = self.role_for(target)
        settings = access.get("settings") if isinstance(access.get("settings"), dict) else {}
        legacy_recipients = {str(value) for value in access.get("notification_recipients", [])}
        chat_id = str(record.get("chat_id") or target)
        legacy_wheels = record.get("notifications_enabled")
        if legacy_wheels is None:
            legacy_wheels = (
                chat_id in legacy_recipients
                if legacy_recipients
                else bool(settings.get("wheel_notifications", True))
            )
        defaults = {
            "wheels": bool(legacy_wheels),
            "daily_reports": (
                bool(settings.get("daily_reports", True))
                if role in {"owner", "admin"}
                else False
            ),
            "weekly_reports": (
                bool(settings.get("weekly_reports", True))
                if role in {"owner", "admin"}
                else False
            ),
            "admin_system": role in {"owner", "admin"},
            "admin_sources": role in {"owner", "admin"},
            "admin_requests": role in {"owner", "admin"},
        }
        raw = record.get("notification_preferences")
        if isinstance(raw, dict):
            for key in defaults:
                if key in raw:
                    defaults[key] = bool(raw[key])
        if role not in {"owner", "admin"}:
            for key, _, _ in ADMIN_NOTIFICATION_OPTIONS:
                defaults[key] = False
        return defaults

    def show_settings(self) -> None:
        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
        ]
        lines = [
            "⚙️ <b>Настройки</b>",
            "",
            "Все виды уведомлений собраны в одном разделе и применяются лично для вашего аккаунта.",
        ]
        if self.is_admin():
            interval = int(
                self.load_access().get("settings", {}).get("monitor_interval_minutes", 5)
            )
            lines.extend(["", f"Интервал постоянной проверки: <b>{interval} мин.</b>"])
            rows.append([{"text": "⏱ Интервал проверки", "callback_data": "page:interval"}])
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_notifications(self) -> None:
        prefs = self.notification_preferences()
        admin = self.is_admin()
        lines = [
            "🔔 <b>Уведомления</b>",
            "",
            "Выберите сообщения, которые хотите получать лично.",
            "Один и тот же сбой отправляется один раз и не повторяется до восстановления.",
            "",
            "<b>Пользовательские</b>",
        ]
        rows: list[list[dict[str, Any]]] = []
        for key, label, description in USER_NOTIFICATION_OPTIONS:
            lines.append(
                f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}"
            )
            rows.append(
                [{
                    "text": f"{self.bool_mark(prefs[key])} {label}",
                    "callback_data": f"notify:{key}",
                }]
            )
        if admin:
            lines.extend(["", "<b>Только для администратора</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append(
                    [{
                        "text": f"{self.bool_mark(prefs[key])} {label}",
                        "callback_data": f"notify:{key}",
                    }]
                )
        else:
            lines.extend([
                "",
                "Системные ошибки, проблемы источников и заявки пользователей обычным пользователям не отправляются.",
            ])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def toggle_notification(self, key: str) -> None:
        allowed = {name for name, _, _ in USER_NOTIFICATION_OPTIONS}
        if self.is_admin():
            allowed.update(name for name, _, _ in ADMIN_NOTIFICATION_OPTIONS)
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
            for admin_key, _, _ in ADMIN_NOTIFICATION_OPTIONS:
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

    def set_admin(self, user_id: str, enabled: bool) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет администраторами")
        access = self.load_access()
        admins = {str(value) for value in access.get("admins", [])}
        if enabled:
            admins.add(user_id)
        else:
            admins.discard(user_id)
        admins.discard(str(access.get("owner_id") or ""))
        access["admins"] = sorted(admins)
        record = access.get("users", {}).get(user_id)
        if isinstance(record, dict):
            prefs = record.get("notification_preferences")
            prefs = dict(prefs) if isinstance(prefs, dict) else {}
            if enabled:
                for key, _, _ in USER_NOTIFICATION_OPTIONS:
                    prefs[key] = True
            for key, _, _ in ADMIN_NOTIFICATION_OPTIONS:
                prefs[key] = bool(enabled)
            record["notification_preferences"] = prefs
        self.save_access(
            "Update Telegram administrators and notification role defaults [skip ci]"
        )

    def transfer_owner(self, user_id: str) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец может передать владение")
        access = self.load_access()
        if user_id not in access.get("users", {}):
            raise ValueError("Новый владелец сначала должен запустить бота")
        old_owner = str(access.get("owner_id") or "")
        access["owner_id"] = user_id
        admins = {str(value) for value in access.get("admins", [])}
        admins.discard(user_id)
        if old_owner and old_owner != user_id:
            admins.add(old_owner)
        access["admins"] = sorted(admins)
        for target in {user_id, old_owner}:
            record = access.get("users", {}).get(target)
            if not isinstance(record, dict):
                continue
            prefs = record.get("notification_preferences")
            prefs = dict(prefs) if isinstance(prefs, dict) else {}
            for key, _, _ in (*USER_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS):
                prefs[key] = True
            record["notification_preferences"] = prefs
            record["notifications_enabled"] = True
        self.save_access("Transfer ownership with administrator notifications enabled [skip ci]")

    def render_page(self, page: str) -> None:
        if page == "notifications":
            self.show_notifications()
            return
        super().render_page(page)

    def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].casefold() if parts else ""
        payload = parts[1].casefold() if len(parts) == 2 else ""
        if command == "/notifications" or (command == "/start" and payload == "notifications"):
            if not self.private_chat(message):
                return
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            self.set_context(chat.get("id"), sender.get("id"))
            self.register_user(message)
            self.set_context(chat.get("id"), sender.get("id"))
            if self.current_role == "blocked" or not self.can_view():
                self.send("Настройки сейчас недоступны.")
                return
            self.navigation[str(self.current_user_id)] = ["menu", "settings"]
            self.show_notifications()
            return
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data.startswith("notify:"):
            message = query.get("message") if isinstance(query, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            sender = query.get("from") if isinstance(query, dict) else None
            self.set_context(
                chat.get("id") if isinstance(chat, dict) else None,
                sender.get("id") if isinstance(sender, dict) else None,
            )
            try:
                self.toggle_notification(data.split(":", 1)[1])
                self.answer(str(query.get("id") or ""), "Настройка изменена")
                self.show_notifications()
            except PermissionError:
                self.answer(str(query.get("id") or ""), "Недоступно для вашей роли")
            except Exception as exc:
                print(f"ERROR notification preference: {type(exc).__name__}: {exc}")
                self.answer(str(query.get("id") or ""), "Не удалось сохранить")
            return
        super().handle_callback(query)


def self_test() -> None:
    bot = TelegramPanelRuntimeV21()
    bot.miniapp_deployment = lambda: {"status": "deployed", "url": MINIAPP_URL}  # type: ignore[method-assign]
    bot.bot_username = lambda: ""  # type: ignore[method-assign]
    url = bot.miniapp_url_for_chat()
    assert url.startswith(MINIAPP_URL)
    assert f"release={MINIAPP_RELEASE}" in url
    user_callbacks = [
        button.get("callback_data")
        for row in bot.compact_menu_rows(False)
        for button in row
    ]
    assert "page:settings" in user_callbacks
    assert len(USER_NOTIFICATION_OPTIONS) == 3
    assert len(ADMIN_NOTIFICATION_OPTIONS) == 3
    print("admin_panel_runtime_v21 current UI self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV21().run()


if __name__ == "__main__":
    raise SystemExit(main())
