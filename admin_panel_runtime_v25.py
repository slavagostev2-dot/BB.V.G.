from __future__ import annotations

import argparse
import html
import inspect
import threading
from datetime import datetime, timezone
from typing import Any

import admin_bot as legacy
import bot_private_state
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v24 import TelegramPanelRuntimeV24
from admin_panel_v2 import default_access

UTC = timezone.utc


class TelegramPanelRuntimeV25(TelegramPanelRuntimeV24):
    """Bot-only control center with persistent encrypted Telegram user state."""

    def __init__(self) -> None:
        super().__init__()
        self._bot_state_lock = threading.RLock()
        self._bot_bundle: dict[str, Any] | None = None

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

    def _load_bot_bundle(self, force: bool = False) -> dict[str, Any]:
        with self._bot_state_lock:
            if self._bot_bundle is not None and not force:
                return self._bot_bundle
            bundle = bot_private_state.load_file(
                access_default=self._bootstrap_access(),
                source_requests_default=default_source_requests(),
            )
            bundle["access"] = self._bootstrap_access(
                bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
            )
            requests = bundle.get("source_requests")
            bundle["source_requests"] = (
                requests if isinstance(requests, dict) else default_source_requests()
            )
            bundle["version"] = 1
            self._bot_bundle = bundle
            return bundle

    def _save_bot_bundle(self, message: str) -> bool:
        """Save locally first; a transient GitHub error must not block a user button."""
        with self._bot_state_lock:
            bundle = self._load_bot_bundle()
            text = bot_private_state.save_file(bundle)
            try:
                self.update_file(
                    bot_private_state.STATE_PATH.name,
                    text,
                    message,
                )
            except Exception as exc:
                print(
                    "WARNING deferred bot private state persistence: "
                    f"{type(exc).__name__}: {exc}"
                )
                return False
            return True

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
        requests = value.get("requests") if isinstance(value, dict) else None
        return {
            "version": 1,
            "requests": requests if isinstance(requests, dict) else {},
        }

    def save_source_requests(self, value: dict[str, Any], message: str) -> None:
        bundle = self._load_bot_bundle()
        requests = value.get("requests") if isinstance(value, dict) else None
        bundle["source_requests"] = {
            "version": 1,
            "requests": requests if isinstance(requests, dict) else {},
        }
        self._save_bot_bundle(message)

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        if admin:
            return [
                [
                    {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                    {"text": "🔥 Активные колёса", "callback_data": "page:active"},
                ],
                [
                    {"text": "📡 Источники", "callback_data": "page:sources"},
                    {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
                ],
                [
                    {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                    {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                ],
            ]
        return [
            [
                {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
            ],
            [{"text": "📡 Источники", "callback_data": "page:sources"}],
        ]

    def show_sources(self) -> None:
        registry = self.load_source_registry()
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        if not int(summary.get("total", 0) or 0):
            registry = self.source_registry_fallback()
            summary = registry["summary"]
        sources = registry.get("sources") if isinstance(registry.get("sources"), list) else []
        problems = [
            row
            for row in sources
            if isinstance(row, dict) and str(row.get("status") or "") != "available"
        ]
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"Всего в едином реестре: <b>{int(summary.get('total', 0) or 0)}</b>",
            f"Проверено: <b>{int(summary.get('checked', 0) or 0)}</b>",
            f"Доступно: <b>{int(summary.get('available', 0) or 0)}</b>",
            f"Недоступно: <b>{int(summary.get('unavailable', 0) or 0)}</b>",
            f"Ожидает первой проверки: <b>{int(summary.get('pending', 0) or 0)}</b>",
            "",
            "Основной и ночной режимы входят в один реестр: каждый источник отображается один раз.",
        ]
        if problems:
            lines.extend(["", "<b>Требуют внимания</b>"])
            for row in problems[:12]:
                username = str(row.get("username") or "неизвестно")
                reason = str(row.get("reason") or "нет данных")[:180]
                lines.append(f"• @{html.escape(username)} — {html.escape(reason)}")
            if len(problems) > 12:
                lines.append(f"• ещё {len(problems) - 12}")
        rows = [
            [
                {"text": "🔄 Обновить реестр", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
            ],
            [
                {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
            ],
        ]
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def handle_callback(self, query: dict[str, Any]) -> None:
        # A user can first meet the bot through a wheel's inline button. Register
        # that Telegram user before the inherited permission and participation logic.
        message = query.get("message") if isinstance(query, dict) else None
        message = message if isinstance(message, dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = query.get("from") if isinstance(query, dict) else None
        sender = sender if isinstance(sender, dict) else {}
        registration_message = {"chat": chat, "from": sender}
        if self.private_chat(registration_message) and sender.get("id"):
            self.set_context(chat.get("id"), sender.get("id"))
            self.register_user(registration_message)
            self.set_context(chat.get("id"), sender.get("id"))
        super().handle_callback(query)

    def render_page(self, page: str) -> None:
        if page == "app":
            self.send(
                "📦 <b>Приложение временно отключено</b>\n\n"
                "Рабочий контур BB V.G. сейчас находится только в Telegram-боте.",
                reply_markup=self.with_nav(),
            )
            return
        super().render_page(page)


def self_test() -> None:
    bot_private_state.self_test()
    admin_callbacks = {
        button.get("callback_data")
        for row in TelegramPanelRuntimeV25.compact_menu_rows(True)
        for button in row
    }
    assert "page:app" not in admin_callbacks
    assert "page:ranking" not in admin_callbacks
    assert "page:discovery" in admin_callbacks
    assert "page:intelligence" in admin_callbacks
    source = inspect.getsource(TelegramPanelRuntimeV25.handle_callback)
    assert "register_user" in source
    assert "private_chat" in source

    panel = TelegramPanelRuntimeV25()
    panel._bot_bundle = bot_private_state.default_bundle(
        panel._bootstrap_access(), default_source_requests()
    )
    panel._save_bot_bundle = lambda message: True  # type: ignore[method-assign]
    panel.send = lambda *args, **kwargs: {"ok": True}  # type: ignore[method-assign]
    panel.answer = lambda *args, **kwargs: None  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "callback-test",
            "from": {"id": 2, "username": "button_user", "first_name": "Button"},
            "message": {"message_id": 1, "chat": {"id": 2, "type": "private"}},
            "data": "nav:home",
        }
    )
    assert "2" in panel.access.get("users", {})
    assert panel.role_for("2") == "user"
    print("admin_panel_runtime_v25 bot-only recovery self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV25().run()


if __name__ == "__main__":
    raise SystemExit(main())
