from __future__ import annotations

import argparse
from typing import Any, Callable

from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.runtime import self_test as _runtime_self_test


class TelegramPanelRuntimeV41(TelegramPanelRuntime):
    """Production v41 entrypoint with compact single-message navigation."""

    RUNTIME_VERSION = 41

    @staticmethod
    def _without_callbacks(
        reply_markup: dict[str, Any] | None,
        blocked: set[str],
    ) -> dict[str, Any] | None:
        if not isinstance(reply_markup, dict):
            return reply_markup
        rows: list[list[dict[str, Any]]] = []
        for row in reply_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            filtered = [
                dict(button)
                for button in row
                if isinstance(button, dict)
                and str(button.get("callback_data") or "") not in blocked
            ]
            if filtered:
                rows.append(filtered)
        result = dict(reply_markup)
        result["inline_keyboard"] = rows
        return result

    def _render_with_filtered_callbacks(
        self,
        renderer: Callable[[], None],
        blocked: set[str],
    ) -> None:
        original_send = self.send

        def filtered_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            return original_send(
                text,
                reply_markup=self._without_callbacks(reply_markup, blocked),
                chat_id=chat_id,
            )

        self.send = filtered_send  # type: ignore[method-assign]
        try:
            renderer()
        finally:
            self.send = original_send  # type: ignore[method-assign]

    def show_control(self) -> None:
        if not self.is_admin():
            self.send(
                "Управление доступно только администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        rows = [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "✅ Проверить работу системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]
        self.send(
            "🛠 <b>Управление</b>\n\nВыберите действие.",
            reply_markup=self.with_nav(rows),
        )

    def show_settings(self) -> None:
        # System status belongs to Control, not Settings.
        self._render_with_filtered_callbacks(
            lambda: super(TelegramPanelRuntimeV41, self).show_settings(),
            {"page:status"},
        )

    def show_status(self) -> None:
        # Manual source check belongs to Control; status page only reports state.
        self._render_with_filtered_callbacks(
            lambda: super(TelegramPanelRuntimeV41, self).show_status(),
            {"control:monitor"},
        )

    def show_more(self) -> None:
        # Keep the same single owner for the system-status entry point.
        self._render_with_filtered_callbacks(
            lambda: super(TelegramPanelRuntimeV41, self).show_more(),
            {"page:status"},
        )

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

    def _mark_personal_from_notification(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        token = data.split(":", 2)[2]
        context = self.snapshot().state.get("button_contexts", {}).get(token)
        if not isinstance(context, dict):
            raise ValueError("Контекст кнопки устарел")
        key = str(context.get("wheel_key") or context.get("identifier") or "").casefold()
        if not key:
            raise ValueError("Не удалось определить колесо")
        self.mark_personal_participation(key)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")

        if data.startswith("wheel:part:"):
            message = query.get("message") if isinstance(query, dict) else None
            message = message if isinstance(message, dict) else {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                self._prepare_callback_user(query)
                key = data.split(":", 2)[2]
                self.mark_personal_participation(key)
                self.answer(query_id, "Ваше участие отмечено")
                # Re-render the same Active Wheels message. No navigation occurs.
                self.show_active()
            except Exception as exc:
                print(f"ERROR active participation {data}: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось выполнить действие")
            finally:
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith("bb:p:"):
            try:
                self._prepare_callback_user(query)
                self._mark_personal_from_notification(query)
                self.answer(query_id, "Ваше участие отмечено")
                self._delete_callback_message(query)
            except Exception as exc:
                print(f"ERROR notification participation {data}: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось выполнить действие")
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
    panel.mark_personal_participation = lambda key: events.append(("participate", str(key)))  # type: ignore[method-assign]
    panel.answer = lambda query_id, text: events.append(("answer", str(text)))  # type: ignore[method-assign]
    panel.show_active = lambda page=0: events.append(("active", str(page)))  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "q-active",
            "data": "wheel:part:wheel-a",
            "message": {"message_id": 77, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("participate", "wheel-a") in events
    assert ("active", "0") in events
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

    assert TelegramPanelRuntimeV41._without_callbacks(
        {
            "inline_keyboard": [
                [
                    {"text": "status", "callback_data": "page:status"},
                    {"text": "notifications", "callback_data": "page:notifications"},
                ]
            ]
        },
        {"page:status"},
    ) == {
        "inline_keyboard": [
            [{"text": "notifications", "callback_data": "page:notifications"}]
        ]
    }

    print("BB V.G. v41 compact UI and participation self-test passed")


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
