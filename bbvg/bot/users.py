from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

import privacy_retention
from bbvg.bot.wheels import WheelInteractionRuntime

MINIAPP_RELEASE = "5.10.0"
MINIAPP_URL = "https://slavagostev2-betboom-monitor.pages.dev/"

# Kept for compatibility with modules that still import the original v21 names.
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


class UserManagementRuntime(WheelInteractionRuntime):
    """User registration, roles and base personal notification preferences."""

    def register_user(self, message: dict[str, Any]) -> str:
        sender = message.get("from") if isinstance(message, dict) else None
        user_id = str(sender.get("id") or "") if isinstance(sender, dict) else ""
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        known_user = bool(user_id and user_id in users)
        if user_id and not known_user:
            access = self.load_access(force=True)
            users = access.get("users") if isinstance(access.get("users"), dict) else {}
            known_user = user_id in users

        role = super().register_user(message)
        if user_id and not known_user:
            self.notify_owner_about_new_user(user_id)
        return role

    def handle_update(self, update: dict[str, Any]) -> None:
        membership = update.get("my_chat_member") if isinstance(update, dict) else None
        if isinstance(membership, dict):
            chat = membership.get("chat")
            sender = membership.get("from")
            new_member = membership.get("new_chat_member")
            status = (
                str(new_member.get("status") or "")
                if isinstance(new_member, dict)
                else ""
            )
            if (
                isinstance(chat, dict)
                and str(chat.get("type") or "") == "private"
                and isinstance(sender, dict)
                and not sender.get("is_bot")
                and status not in {"left", "kicked"}
            ):
                self.set_context(chat.get("id"), sender.get("id"))
                self.register_user({"chat": chat, "from": sender})
            return
        super().handle_update(update)

    def notify_owner_about_new_user(self, user_id: str) -> None:
        access = self.load_access()
        owner_id = str(access.get("owner_id") or "")
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(user_id) if isinstance(users.get(user_id), dict) else {}
        owner = users.get(owner_id) if isinstance(users.get(owner_id), dict) else {}
        owner_chat_id = str(owner.get("chat_id") or owner_id)
        if not owner_chat_id or owner_id == user_id or not record:
            return
        full_name = _display_name(record, user_id) or "Пользователь"
        username = str(record.get("username") or "").strip()
        username_line = f"\nUsername: @{html.escape(username)}" if username else ""
        try:
            self.send(
                "👤 <b>Новый пользователь BB V.G.</b>\n\n"
                f"{html.escape(full_name)}{username_line}\n"
                f"Telegram ID: <code>{html.escape(user_id)}</code>\n\n"
                "Пользователь добавлен в раздел «Доступ и администраторы».",
                chat_id=owner_chat_id,
                reply_markup={
                    "inline_keyboard": [[{
                        "text": "👥 Открыть список пользователей",
                        "callback_data": "page:access",
                    }]]
                },
            )
        except Exception as exc:
            # Registration must succeed even if this informational message fails.
            print(f"WARNING new user notification: {type(exc).__name__}: {exc}")

    def show_access(self) -> None:
        self.load_access(force=True)
        super().show_access()

    def show_user_detail(self, user_id: str) -> None:
        self.load_access(force=True)
        super().show_user_detail(user_id)

    def show_recipients(self) -> None:
        self.load_access(force=True)
        super().show_recipients()

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
        rows = WheelInteractionRuntime.compact_menu_rows(admin)
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
            rows.append([{
                "text": f"{self.bool_mark(prefs[key])} {label}",
                "callback_data": f"notify:{key}",
            }])
        if admin:
            lines.extend(["", "<b>Только для администратора</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                lines.append(
                    f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append([{
                    "text": f"{self.bool_mark(prefs[key])} {label}",
                    "callback_data": f"notify:{key}",
                }])
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
        self._sync_recipient(access, record, str(self.current_user_id))
        self.save_access(
            f"Update personal notification preferences for {self.current_user_id} [skip ci]"
        )
        self.dispatch("monitor.yml", {"continuous": "true"})

    @staticmethod
    def _sync_recipient(
        access: dict[str, Any],
        record: dict[str, Any],
        fallback_user_id: str,
    ) -> None:
        chat_id = str(record.get("chat_id") or fallback_user_id)
        recipients = {str(value) for value in access.get("notification_recipients", [])}
        if bool(record.get("notifications_enabled", True)):
            recipients.add(chat_id)
        else:
            recipients.discard(chat_id)
        access["notification_recipients"] = sorted(recipients)

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
        self.save_access(
            "Transfer ownership with administrator notifications enabled [skip ci]"
        )

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
        if command == "/notifications" or (
            command == "/start" and payload == "notifications"
        ):
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


class UserSettingsMixin:
    """Final v33-v35 settings, privacy and owner notification controls."""

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
            lines.extend([
                "",
                "Удаление данных владельца возможно только после передачи владения другому пользователю.",
            ])
        else:
            rows.append([{"text": "🗑 Удалить мои данные", "callback_data": "privacy:delete:ask"}])
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
            enabled = bool(prefs.get(key, False))
            lines.append(
                f"{self.bool_mark(enabled)} {html.escape(label)} — {html.escape(description)}"
            )
            rows.append([{
                "text": f"{self.bool_mark(enabled)} {label}",
                "callback_data": f"notify:{key}",
            }])
        if admin:
            lines.extend(["", "<b>Сводки</b>"])
            for key, label, description in ALL_SUMMARY_NOTIFICATION_OPTIONS:
                enabled = bool(prefs.get(key, False))
                lines.append(
                    f"{self.bool_mark(enabled)} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append([{
                    "text": f"{self.bool_mark(enabled)} {label}",
                    "callback_data": f"notify:{key}",
                }])
            lines.extend(["", "<b>Административные</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                enabled = bool(prefs.get(key, False))
                lines.append(
                    f"{self.bool_mark(enabled)} {html.escape(label)} — {html.escape(description)}"
                )
                rows.append([{
                    "text": f"{self.bool_mark(enabled)} {label}",
                    "callback_data": f"notify:{key}",
                }])
        else:
            lines.extend(["", "Сводки и служебные уведомления доступны только администраторам."])
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
        UserManagementRuntime._sync_recipient(access, record, str(self.current_user_id))
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
        text = (
            f"👤 <b>{html.escape(_display_name(record, user_id))}</b>\n\n"
            f"Telegram ID: <code>{html.escape(user_id)}</code>\n"
            f"Роль: {self.role_name(role)}\n"
            f"Уведомления: <b>{enabled_count} из {total_count}</b>\n"
            f"Последняя активность: {self.fmt_dt(record.get('last_seen_at'))}"
        )
        rows: list[list[dict[str, str]]] = [[{
            "text": "🔔 Управлять уведомлениями",
            "callback_data": f"usernotifications:{user_id}",
        }]]
        if role == "user":
            rows.append([{
                "text": "Сделать администратором",
                "callback_data": f"access:promote:{user_id}",
            }])
        elif role == "admin":
            rows.append([{
                "text": "Убрать права администратора",
                "callback_data": f"access:demote:{user_id}",
            }])
        if role != "owner":
            rows.append([{
                "text": "👑 Передать владение",
                "callback_data": f"access:transferask:{user_id}",
            }])
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
        lines = [
            "🔔 <b>Уведомления пользователя</b>",
            "",
            f"Пользователь: <b>{html.escape(_display_name(record, user_id))}</b>",
            f"Роль: {self.role_name(role)}",
            "",
            "Изменения применяются только к этому Telegram-аккаунту.",
        ]
        rows: list[list[dict[str, str]]] = []
        wheel_keys = {key for key, _, _ in WHEEL_NOTIFICATION_OPTIONS}
        summary_keys = {key for key, _, _ in ALL_SUMMARY_NOTIFICATION_OPTIONS}
        current_section = ""
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
            enabled = bool(prefs.get(key, False))
            lines.append(
                f"{self.bool_mark(enabled)} {html.escape(label)} — {html.escape(description)}"
            )
            rows.append([{
                "text": f"{self.bool_mark(enabled)} {label}",
                "callback_data": f"usernotify:{user_id}:{key}",
            }])
        rows.extend([
            [
                {"text": "✅ Включить все", "callback_data": f"usernotifyall:{user_id}:on"},
                {"text": "⛔ Отключить все", "callback_data": f"usernotifyall:{user_id}:off"},
            ],
            [{"text": "👤 К пользователю", "callback_data": f"page:user:{user_id}"}],
        ])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def _save_user_preferences(
        self,
        user_id: str,
        prefs: dict[str, bool],
        message: str,
    ) -> None:
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(user_id)
        if not isinstance(record, dict):
            raise ValueError("Пользователь не найден")
        record["notification_preferences"] = prefs
        record["notifications_enabled"] = bool(prefs.get("wheels", True))
        UserManagementRuntime._sync_recipient(access, record, user_id)
        self.save_access(message)
        self.dispatch("monitor.yml", {"continuous": "true"})

    def set_user_notification(
        self,
        user_id: str,
        key: str,
        enabled: bool | None = None,
    ) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет уведомлениями пользователей")
        role = self.role_for(user_id)
        allowed = {name for name, _, _ in self._notification_options_for_role(role)}
        if key not in allowed:
            raise PermissionError("Этот вид уведомлений недоступен для роли пользователя")
        prefs = self.notification_preferences(user_id)
        prefs[key] = (not bool(prefs.get(key, False))) if enabled is None else bool(enabled)
        if role not in {"owner", "admin"}:
            for admin_key, _, _ in (*ALL_SUMMARY_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS):
                prefs[admin_key] = False
        self._save_user_preferences(
            user_id,
            prefs,
            f"Owner updated notification {key} for Telegram user {user_id} [skip ci]",
        )

    def set_all_user_notifications(self, user_id: str, enabled: bool) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет уведомлениями пользователей")
        role = self.role_for(user_id)
        prefs = self.notification_preferences(user_id)
        for key, _, _ in self._notification_options_for_role(role):
            prefs[key] = bool(enabled)
        if role not in {"owner", "admin"}:
            for admin_key, _, _ in (*ALL_SUMMARY_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS):
                prefs[admin_key] = False
        self._save_user_preferences(
            user_id,
            prefs,
            f"Owner {'enabled' if enabled else 'disabled'} all notifications "
            f"for Telegram user {user_id} [skip ci]",
        )

    def render_page(self, page: str) -> None:
        if page.startswith("user_notifications:"):
            self.show_user_notifications(page.split(":", 1)[1])
            return
        super().render_page(page)

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
                    reply_markup=self.with_nav([[
                        {"text": "Удалить", "callback_data": "privacy:delete:confirm"},
                        {"text": "Отмена", "callback_data": "privacy:delete:cancel"},
                    ]]),
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
    bot = UserManagementRuntime()
    bot.miniapp_deployment = lambda: {  # type: ignore[method-assign]
        "status": "deployed",
        "url": MINIAPP_URL,
    }
    bot.bot_username = lambda: ""  # type: ignore[method-assign]
    url = bot.miniapp_url_for_chat()
    assert url.startswith(MINIAPP_URL)
    assert f"release={MINIAPP_RELEASE}" in url
    assert len(USER_NOTIFICATION_OPTIONS) == 3
    assert len(ADMIN_NOTIFICATION_OPTIONS) == 3
    assert {key for key, _, _ in WHEEL_NOTIFICATION_OPTIONS} == {
        "wheels",
        "wheel_final_reminders",
        "wheel_draw_alerts",
    }

    access = {
        "owner_id": "1",
        "admins": [],
        "blocked_users": [],
        "notification_recipients": [],
        "settings": {"public_panel": True, "notifications": True},
        "users": {
            "1": {"id": "1", "chat_id": "101", "first_name": "Владелец"},
            "2": {"id": "2", "chat_id": "202", "first_name": "Новый", "username": "new_user"},
        },
    }
    load_calls: list[bool] = []
    sent: list[tuple[str, dict[str, Any]]] = []
    bot.load_access = lambda force=False: load_calls.append(force) or access  # type: ignore[method-assign]
    bot.send = lambda text, **kwargs: sent.append((text, kwargs)) or {}  # type: ignore[method-assign]
    bot.current_role = "owner"
    bot.notify_owner_about_new_user("2")
    assert sent and sent[-1][1]["chat_id"] == "101"
    assert "@new_user" in sent[-1][0]

    class SettingsPanel(UserSettingsMixin, UserManagementRuntime):
        pass

    settings = SettingsPanel()
    settings.current_user_id = "1"
    settings.current_chat_id = "101"
    settings.current_role = "owner"
    settings.is_owner = lambda: True  # type: ignore[method-assign]
    settings.is_admin = lambda: True  # type: ignore[method-assign]
    settings.role_for = lambda user_id: "owner" if str(user_id) == "1" else "user"  # type: ignore[method-assign]
    settings.load_access = lambda force=False: access  # type: ignore[method-assign]
    saved: list[str] = []
    dispatched: list[tuple[str, dict[str, str]]] = []
    settings.save_access = lambda message="": saved.append(message)  # type: ignore[method-assign]
    settings.dispatch = lambda workflow, inputs=None: dispatched.append(  # type: ignore[method-assign]
        (workflow, dict(inputs or {}))
    )
    settings.set_user_notification("2", "wheels", False)
    assert access["users"]["2"]["notifications_enabled"] is False
    assert saved and dispatched == [("monitor.yml", {"continuous": "true"})]
    assert settings.notification_preferences("2")["wheel_final_reminders"] is True
    print("BB V.G. user management and settings subsystem self-test passed")


if __name__ == "__main__":
    self_test()
