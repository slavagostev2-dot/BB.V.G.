from __future__ import annotations

import argparse
import html
from pathlib import Path
from typing import Any

import privacy_retention
from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.runtime import self_test as _runtime_self_test
from bbvg.bot.users import UserManagementRuntime


class TelegramPanelRuntimeV41(TelegramPanelRuntime):
    """Production v41 entrypoint with compact single-message navigation."""

    RUNTIME_VERSION = 41

    def show_control(self) -> None:
        if not self.is_admin():
            self.send(
                "Управление доступно только администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        rows = self.control_menu_rows()
        self.send(
            "🛠 <b>Управление</b>\n\nВыберите действие.",
            reply_markup=self.with_nav(rows),
        )

    def status_action_rows(self) -> list[list[dict[str, str]]]:
        # Manual checks remain available only in the Control section.
        return []

    def show_analytics(self, days: int = 1) -> None:
        current_errors = int(self._monitor_status().get("source_errors", 0) or 0)
        original_send = self.send

        def analytics_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            text = text.replace(
                "⚠️ Ошибок источников:",
                "ℹ️ Разовых ошибок проверок за период:",
            )
            marker = "ℹ️ Разовых ошибок проверок за период:"
            if marker in text:
                lines = text.splitlines()
                for index, line in enumerate(lines):
                    if marker in line:
                        lines.insert(
                            index + 1,
                            f"{'✅' if current_errors == 0 else '⚠️'} Проблемных источников сейчас: <b>{current_errors}</b>",
                        )
                        break
                text = "\n".join(lines)
            return original_send(text, reply_markup=reply_markup, chat_id=chat_id)

        self.send = analytics_send  # type: ignore[method-assign]
        try:
            super().show_analytics(days)
        finally:
            self.send = original_send  # type: ignore[method-assign]

    def show_disabled_features(self) -> None:
        text = (
            "⛔ <b>Отключённый функционал</b>\n\n"
            "• <b>Ручное указание времени</b> — отключено: бот использует время BetBoom API; "
            "если серверное время неизвестно, действует штатное двухчасовое окно.\n"
            "• <b>Общее «Участвую»</b> — отключено: нажатие отмечает участие только для "
            "конкретного пользователя, включая владельца и администраторов.\n"
            "• <b>Ручные «Завершено» и «Неактивное»</b> — отключены в пользовательском "
            "интерфейсе: жизненный цикл колеса определяется актуальной серверной проверкой.\n"
            "• <b>Скрытие колеса отдельным пользователем</b> — отключено: список активных колёс "
            "общий, а отметка участия персональная.\n"
            "• <b>Параллельный Legacy HTML-checker</b> — отключён, чтобы одно колесо не получало "
            "противоречивые статусы из двух независимых проверок.\n"
            "• <b>Mini App</b> — архивировано: рабочий интерфейс сейчас находится в Telegram-боте.\n"
            "• <b>Автоматические ежедневные, недельные и месячные сводки</b> — отключены; "
            "сводка формируется по запросу в разделе аналитики."
        )
        self.send(text, reply_markup=self.with_nav())

    @staticmethod
    def _user_display_name(record: dict[str, Any], user_id: str) -> str:
        full_name = " ".join(
            value
            for value in (
                str(record.get("first_name") or "").strip(),
                str(record.get("last_name") or "").strip(),
            )
            if value
        )
        username = str(record.get("username") or "").strip().lstrip("@")
        return full_name or (f"@{username}" if username else user_id)

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
        options = self._notification_options_for_role(role)
        enabled_count = sum(1 for key, _, _ in options if prefs.get(key, False))
        blocked = user_id in {str(value) for value in access.get("blocked_users", [])}
        status_line = "Удалён из доступа" if blocked else self.role_name(role)
        text = (
            f"👤 <b>{html.escape(self._user_display_name(record, user_id))}</b>\n\n"
            f"Telegram ID: <code>{html.escape(user_id)}</code>\n"
            f"Статус: {html.escape(status_line)}\n"
            f"Уведомления: <b>{enabled_count} из {len(options)}</b>\n"
            f"Последняя активность: {self.fmt_dt(record.get('last_seen_at'))}"
        )
        rows: list[list[dict[str, str]]] = []
        if not blocked:
            rows.append([{
                "text": "🔔 Управлять уведомлениями",
                "callback_data": f"usernotifications:{user_id}",
            }])
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
        if role != "owner":
            if not blocked:
                rows.append([{
                    "text": "🚫 Удалить пользователя из бота",
                    "callback_data": f"userremove:ask:{user_id}",
                }])
            rows.append([{
                "text": "🗑 Удалить и стереть все данные",
                "callback_data": f"usererase:ask:{user_id}",
            }])
        self.send(text, reply_markup=self.with_nav(rows))

    def remove_user_from_bot(self, user_id: str) -> bool:
        if not self.is_owner():
            raise PermissionError("Только владелец может удалять пользователей")
        access = self.load_access(force=True)
        target = str(user_id or "")
        if not target or target not in access.get("users", {}):
            raise ValueError("Пользователь не найден")
        if str(access.get("owner_id") or "") == target:
            raise PermissionError("Нельзя удалить владельца до передачи владения")
        record = access.get("users", {}).get(target)
        if not isinstance(record, dict):
            raise ValueError("Пользователь не найден")
        changed = False
        admins = [str(value) for value in access.get("admins", []) if str(value) != target]
        if admins != access.get("admins", []):
            access["admins"] = admins
            changed = True
        blocked = {str(value) for value in access.get("blocked_users", [])}
        if target not in blocked:
            blocked.add(target)
            access["blocked_users"] = sorted(blocked)
            changed = True
        chat_id = str(record.get("chat_id") or target)
        recipients = [
            str(value)
            for value in access.get("notification_recipients", [])
            if str(value) not in {target, chat_id}
        ]
        if recipients != access.get("notification_recipients", []):
            access["notification_recipients"] = recipients
            changed = True
        prefs = record.get("notification_preferences")
        if isinstance(prefs, dict):
            disabled = {str(key): False for key in prefs}
            if disabled != prefs:
                record["notification_preferences"] = disabled
                changed = True
        if record.get("notifications_enabled") is not False:
            record["notifications_enabled"] = False
            changed = True
        if changed:
            self.save_access(f"Owner removed Telegram user {target} from bot [skip ci]")
            self.dispatch("monitor.yml", {"continuous": "true", "replace": "true"})
        return changed

    def erase_user_completely(self, user_id: str) -> bool:
        if not self.is_owner():
            raise PermissionError("Только владелец может удалять данные пользователей")
        target = str(user_id or "")
        if not target:
            raise ValueError("Пользователь не указан")
        bundle = self._load_bot_bundle(force=True)
        access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
        if str(access.get("owner_id") or "") == target:
            raise PermissionError("Нельзя удалить данные владельца до передачи владения")
        changed = privacy_retention.delete_user_data(bundle, target)
        if changed:
            self._save_bot_bundle(
                f"Owner permanently deleted Telegram user data {target} [skip ci]"
            )
            with self.access_lock:
                self.access = {}
                self.access_loaded = False
            self.dispatch("monitor.yml", {"continuous": "true", "replace": "true"})
        return changed

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")

        if data.startswith("userremove:") or data.startswith("usererase:"):
            self._prepare_callback_user(query)
            try:
                if not self.is_owner():
                    raise PermissionError("Недоступно")
                kind, action, target = data.split(":", 2)
                access = self.load_access(force=True)
                record = access.get("users", {}).get(target)
                if not isinstance(record, dict):
                    raise ValueError("Пользователь не найден")
                if str(access.get("owner_id") or "") == target:
                    raise PermissionError("Сначала передайте владение другому пользователю")
                name = html.escape(self._user_display_name(record, target))
                if action == "ask":
                    if kind == "userremove":
                        self.answer(query_id, "Нужно подтверждение")
                        self.send(
                            f"🚫 <b>Удалить {name} из бота?</b>\n\n"
                            "Доступ будет закрыт, роль администратора снята и уведомления отключены. "
                            "Сохранённые персональные данные останутся, поэтому их можно удалить отдельно.",
                            reply_markup=self.with_nav([[
                                {"text": "Да, удалить из бота", "callback_data": f"userremove:confirm:{target}"},
                                {"text": "Отмена", "callback_data": f"page:user:{target}"},
                            ]]),
                        )
                    else:
                        self.answer(query_id, "Нужно подтверждение")
                        self.send(
                            f"🗑 <b>Полностью стереть данные {name}?</b>\n\n"
                            "Профиль, настройки, личные отметки участия и ожидающие заявки будут удалены. "
                            "Обработанные заявки сохранятся только в обезличенном виде. Это действие необратимо.",
                            reply_markup=self.with_nav([[
                                {"text": "Да, стереть данные", "callback_data": f"usererase:confirm:{target}"},
                                {"text": "Отмена", "callback_data": f"page:user:{target}"},
                            ]]),
                        )
                    return
                if action == "confirm":
                    if kind == "userremove":
                        changed = self.remove_user_from_bot(target)
                        self.answer(query_id, "Пользователь удалён из бота" if changed else "Уже удалён")
                    else:
                        changed = self.erase_user_completely(target)
                        self.answer(query_id, "Данные пользователя стёрты" if changed else "Данные уже удалены")
                    self.show_access()
                    return
            except PermissionError as exc:
                self.answer(query_id, "Недоступно")
                self.send(html.escape(str(exc)), reply_markup=self.with_nav())
            except Exception as exc:
                print(f"ERROR owner user deletion {data}: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось выполнить удаление")
                self.send(
                    "⚠️ Не удалось безопасно изменить данные пользователя.",
                    reply_markup=self.with_nav(),
                )
            return

        super().handle_callback(query)


def self_test() -> None:
    _runtime_self_test()

    captured: list[tuple[str, dict[str, Any]]] = []
    panel = TelegramPanelRuntimeV41.__new__(TelegramPanelRuntimeV41)
    panel.is_admin = lambda: True  # type: ignore[method-assign]
    panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.show_control()
    markup = captured[-1][1]["reply_markup"]
    callbacks = [
        str(button.get("callback_data") or "")
        for row in markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict)
    ]
    assert "control:intelligence" not in callbacks
    assert "control:nightly" not in callbacks
    assert "control:daily" not in callbacks
    assert "control:monitor" in callbacks
    assert "page:status" in callbacks

    events: list[tuple[str, str]] = []
    panel = TelegramPanelRuntimeV41.__new__(TelegramPanelRuntimeV41)
    panel._edit_message_id = None
    panel._prepare_callback_user = lambda query: events.append(("prepare", str(query.get("data"))))  # type: ignore[method-assign]
    panel.mark_personal_participation = lambda key: (  # type: ignore[method-assign]
        events.append(("participate", str(key))),
        {"changed": True},
    )[1]
    panel.answer = lambda query_id, text: events.append(("answer", str(text)))  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: events.append(("menu", str(clear_stack)))  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "q-active",
            "data": "wheel:part:wheel-a",
            "message": {"message_id": 77, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("participate", "wheel-a") in events
    assert ("menu", "True") in events
    assert panel._edit_message_id is None

    events.clear()
    panel.snapshot = lambda force=False: type(  # type: ignore[method-assign]
        "Snap",
        (),
        {"state": {"button_contexts": {"token": {"wheel_key": "wheel-b"}}}},
    )()
    panel._delete_callback_message = lambda query: events.append(("delete", str(query.get("data"))))  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "q-notify",
            "data": "bb:p:token",
            "message": {"message_id": 78, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("participate", "wheel-b") in events
    assert ("delete", "bb:p:token") in events

    users_source = Path("bbvg/bot/users.py").read_text(encoding="utf-8")
    assert "return WheelInteractionRuntime.compact_menu_rows(admin)" not in users_source
    for name in (
        "_without_callbacks",
        "_render_with_filtered_callbacks",
        "show_settings",
        "show_status",
        "show_more",
    ):
        assert name not in TelegramPanelRuntimeV41.__dict__

    captured.clear()
    panel = TelegramPanelRuntimeV41.__new__(TelegramPanelRuntimeV41)
    panel.current_user_id = "1"
    panel.current_chat_id = "1"
    panel.current_role = "owner"
    panel.navigation = {}
    panel.is_admin = lambda: True  # type: ignore[method-assign]
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.role_for = lambda user_id=None: "owner"  # type: ignore[method-assign]
    panel.load_access = lambda force=False: {  # type: ignore[method-assign]
        "settings": {"monitor_interval_minutes": 5},
        "users": {},
        "owner_id": "1",
        "admins": [],
    }
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.show_settings()
    settings_callbacks = {
        str(button.get("callback_data") or "")
        for row in captured[-1][1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert "page:notifications" in settings_callbacks
    assert "page:status" not in settings_callbacks

    captured.clear()
    panel.show_more()
    more_callbacks = {
        str(button.get("callback_data") or "")
        for row in captured[-1][1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert "page:settings" in more_callbacks
    assert "page:status" not in more_callbacks

    captured.clear()
    panel.snapshot = lambda force=False: type(  # type: ignore[method-assign]
        "Snap",
        (),
        {"fast": ["source"], "nightly": [], "state": {}, "stats": {}, "health": {}},
    )()
    panel._monitor_status = lambda: {  # type: ignore[method-assign]
        "checked_sources": 1,
        "reachable_sources": 1,
        "source_errors": 0,
        "last_successful_iteration_at": "2026-07-25T00:00:00+00:00",
    }
    panel.load_source_registry = lambda: {"summary": {"total": 1}}  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: []  # type: ignore[method-assign]
    panel.show_status()
    status_callbacks = {
        str(button.get("callback_data") or "")
        for row in captured[-1][1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert "refresh:status" in status_callbacks
    assert "control:monitor" not in status_callbacks


    access = {
        "owner_id": "1",
        "admins": ["2"],
        "blocked_users": [],
        "notification_recipients": ["1", "2", "202"],
        "users": {
            "1": {"id": "1", "chat_id": "1"},
            "2": {
                "id": "2",
                "chat_id": "202",
                "notifications_enabled": True,
                "notification_preferences": {"wheels": True, "wheel_final_reminders": True},
            },
        },
    }
    panel = TelegramPanelRuntimeV41.__new__(TelegramPanelRuntimeV41)
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.load_access = lambda force=False: access  # type: ignore[method-assign]
    saved: list[str] = []
    dispatched: list[tuple[str, dict[str, str]]] = []
    panel.save_access = lambda message="": saved.append(message)  # type: ignore[method-assign]
    panel.dispatch = lambda workflow, inputs=None: dispatched.append((workflow, dict(inputs or {})))  # type: ignore[method-assign]
    assert panel.remove_user_from_bot("2")
    assert "2" in access["blocked_users"]
    assert "2" not in access["admins"]
    assert "2" not in access["notification_recipients"]
    assert "202" not in access["notification_recipients"]
    assert access["users"]["2"]["notifications_enabled"] is False
    assert not any(access["users"]["2"]["notification_preferences"].values())
    assert saved and dispatched == [("monitor.yml", {"continuous": "true", "replace": "true"})]

    print("BB V.G. v41 compact UI, participation and owner user deletion self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV41().run()


if __name__ == "__main__":
    raise SystemExit(main())
