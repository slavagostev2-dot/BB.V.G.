from __future__ import annotations

from typing import Any


class TelegramPanelCallbacksV22Mixin:
    def _wheel_key_from_token(self, token: str) -> str:
        context = self.snapshot(force=True).state.get("button_contexts", {}).get(token)
        return str(context.get("wheel_key") or context.get("identifier") or "").casefold() if isinstance(context, dict) else ""

    def handle_message(self, message: dict[str, Any]) -> None:
        chat, sender = message.get("chat"), message.get("from")
        self.set_context(chat.get("id") if isinstance(chat, dict) else None, sender.get("id") if isinstance(sender, dict) else None)
        text = str(message.get("text") or "").strip()
        payload = text.split(maxsplit=1)[1].casefold() if text.casefold().startswith("/start ") else ""
        if payload in {"notifications_on", "notifications_off"}:
            enabled = payload.endswith("_on")
            self.set_user_notifications(enabled)
            self.send(f"🔔 Уведомления {'включены' if enabled else 'отключены'}.", reply_markup=self.with_nav())
            return
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        message, sender = query.get("message"), query.get("from")
        chat = message.get("chat") if isinstance(message, dict) else None
        self.set_context(chat.get("id") if isinstance(chat, dict) else None, sender.get("id") if isinstance(sender, dict) else None)
        data = str(query.get("data") or "")
        try:
            if data in {"page:discovery", "page:intelligence", "page:recipients"}:
                self.answer(query_id, "Открываю единый раздел")
                self.show_sources() if data != "page:recipients" else self.show_settings()
                return
            if data in {"page:report:errors", "report:errors"}:
                self.answer(query_id, "Раздел удалён")
                self.show_stats(1)
                return
            if data == "setting:notifications":
                enabled = self.toggle_user_notifications()
                self.answer(query_id, "Включены" if enabled else "Отключены")
                self.show_settings()
                return
            if data.startswith("source_list:"):
                self.answer(query_id)
                self.show_source_list(int(data.split(":", 1)[1]))
                return
            if data.startswith("source_detail:"):
                self.answer(query_id)
                self.show_source_detail(data.split(":", 1)[1])
                return
            if data.startswith("bb:p:"):
                key = self._wheel_key_from_token(data.split(":", 2)[2])
                if not key:
                    raise ValueError("Контекст кнопки устарел")
                if self.is_admin():
                    self.dispatch_admin_action("confirm_active", f"{key}|{self.current_user_id or 'admin'}")
                    self.answer(query_id, "Подтверждение применяется")
                else:
                    self.answer(query_id, "Участие отмечено" if self.toggle_personal_wheel(key) else "Отметка снята")
                return
            if data.startswith("wheel:part:"):
                key = data.split(":", 2)[2].casefold()
                if self.is_admin():
                    self.dispatch_admin_action("confirm_active", f"{key}|{self.current_user_id or 'admin'}")
                    self.answer(query_id, "Колесо подтверждается")
                else:
                    self.answer(query_id, "Участие отмечено" if self.toggle_personal_wheel(key) else "Отметка снята")
                    self.show_active()
                return
            if data.startswith("bb:x:") or data.startswith("wheel:inactive:"):
                key = data.split(":", 2)[2].casefold()
                if self.is_admin():
                    self.dispatch_admin_action("mark_inactive_global", f"{key}|{self.current_user_id or 'admin'}")
                    self.answer(query_id, "Решение применяется")
                else:
                    self.hide_wheel_for_current_user(key)
                    self.answer(query_id, "Скрыто только у вас")
                return
            super().handle_callback(query)
        except Exception as exc:
            print(f"ERROR BB V.G. v22 callback {data}: {type(exc).__name__}: {exc}")
            self.answer(query_id, "Ошибка выполнения")

    def render_page(self, page: str) -> None:
        if page in {"discovery", "intelligence"}:
            self.show_sources()
            return
        if page == "recipients":
            self.show_settings()
            return
        super().render_page(page)
