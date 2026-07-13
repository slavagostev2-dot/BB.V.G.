from __future__ import annotations

from datetime import datetime
from typing import Any


class TelegramPanelAccessV22Mixin:
    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [
            [{"text": "📊 Статистика", "callback_data": "page:stats:1"}, {"text": "🔥 Активные колёса", "callback_data": "page:active"}],
            [{"text": "📡 Источники", "callback_data": "page:sources"}, {"text": "🏆 Рейтинг источников", "callback_data": "page:ranking"}],
            [{"text": "🔔 Уведомления", "callback_data": "page:settings"}, {"text": "📱 Приложение", "callback_data": "page:app"}],
        ]
        if admin:
            rows.insert(2, [{"text": "⚙️ Управление", "callback_data": "page:settings"}, {"text": "✅ Состояние системы", "callback_data": "page:status"}])
        return rows

    def normalize_access(self, value: dict[str, Any]) -> dict[str, Any]:
        result = super().normalize_access(value)
        users = result.get("users") if isinstance(result.get("users"), dict) else {}
        legacy = {str(x) for x in result.get("notification_recipients", []) if str(x)}
        admins = {str(result.get("owner_id") or ""), *{str(x) for x in result.get("admins", [])}}
        recipients: set[str] = set()
        for user_id, raw in list(users.items()):
            if not isinstance(raw, dict):
                raw = {"id": str(user_id), "chat_id": str(user_id)}
                users[str(user_id)] = raw
            chat_id = str(raw.get("chat_id") or user_id)
            raw.setdefault("notifications_enabled", chat_id in legacy if legacy else True)
            raw["admin_notifications"] = str(user_id) in admins
            if not isinstance(raw.get("personal_wheels"), dict):
                raw["personal_wheels"] = {}
            if raw["notifications_enabled"]:
                recipients.add(chat_id)
        result.update(users=users, notification_recipients=sorted(recipients), version=4)
        return result

    def set_admin(self, user_id: str, enabled: bool) -> None:
        super().set_admin(user_id, enabled)
        access = self.load_access(force=True)
        record = access.setdefault("users", {}).get(str(user_id))
        if isinstance(record, dict):
            record["admin_notifications"] = bool(enabled)
            self.save_access("Sync administrator notifications [skip ci]")

    def transfer_owner(self, user_id: str) -> None:
        super().transfer_owner(user_id)
        access = self.load_access(force=True)
        admins = {str(access.get("owner_id") or ""), *{str(x) for x in access.get("admins", [])}}
        for candidate, record in access.get("users", {}).items():
            if isinstance(record, dict):
                record["admin_notifications"] = str(candidate) in admins
        self.save_access("Sync owner notifications [skip ci]")

    def moderator_chat_ids(self) -> list[str]:
        access = self.load_access(force=True)
        admins = {str(access.get("owner_id") or ""), *{str(x) for x in access.get("admins", [])}}
        result = set()
        for user_id in admins:
            record = access.get("users", {}).get(user_id)
            result.add(str(record.get("chat_id") or user_id) if isinstance(record, dict) else user_id)
        return sorted(x for x in result if x)

    def _user_record(self, force: bool = False) -> dict[str, Any]:
        user_id = str(self.current_user_id or "")
        access = self.load_access(force=force)
        record = access.setdefault("users", {}).get(user_id)
        if not isinstance(record, dict):
            record = {"id": user_id, "chat_id": str(self.current_chat_id or user_id), "notifications_enabled": True, "personal_wheels": {}}
            access["users"][user_id] = record
        record.setdefault("notifications_enabled", True)
        record.setdefault("personal_wheels", {})
        return record

    def set_user_notifications(self, enabled: bool) -> None:
        self._user_record(True)["notifications_enabled"] = bool(enabled)
        self.save_access("Update user notifications [skip ci]")

    def toggle_user_notifications(self) -> bool:
        record = self._user_record(True)
        enabled = not bool(record.get("notifications_enabled", True))
        record["notifications_enabled"] = enabled
        self.save_access("Toggle user notifications [skip ci]")
        return enabled

    def personal_wheels(self) -> dict[str, Any]:
        value = self._user_record().get("personal_wheels")
        return value if isinstance(value, dict) else {}

    def toggle_personal_wheel(self, key: str) -> bool:
        key = str(key or "").casefold()
        if not key:
            raise ValueError("Колесо не определено")
        record = self._user_record(True)
        personal = record.setdefault("personal_wheels", {})
        if key in personal:
            personal.pop(key, None)
            enabled = False
        else:
            personal[key] = {"marked_at": datetime.now().astimezone().isoformat()}
            enabled = True
        self.save_access("Update personal wheel mark [skip ci]")
        return enabled

    def _json(self, path: str, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = self.get_json_file(path, default)
        except Exception:
            value = default
        return value if isinstance(value, dict) else default

    def load_registry(self) -> dict[str, Any]:
        return self._json("source_registry.json", {"summary": {}, "sources": {}})

    def load_reputation(self) -> dict[str, Any]:
        return self._json("source_reputation.json", {"ranking": [], "sources": {}, "wheels": {}})

    @staticmethod
    def _case(mapping: object, key: str) -> tuple[str, dict[str, Any]]:
        if isinstance(mapping, dict):
            for name, value in mapping.items():
                if str(name).casefold() == str(key).casefold() and isinstance(value, dict):
                    return str(name), value
        return str(key), {}
