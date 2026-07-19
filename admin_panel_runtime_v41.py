from __future__ import annotations

import argparse
from typing import Any

from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.runtime import self_test as _runtime_self_test


_PARTICIPATION_SUCCESS_ANSWERS = {
    "Ваше участие отмечено",
    "Колесо подтверждается для всех",
}


class TelegramPanelRuntimeV41(TelegramPanelRuntime):
    """Production v41 entrypoint with notification cleanup after participation."""

    RUNTIME_VERSION = 41

    def show_control(self) -> None:
        if not self.is_admin():
            self.send(
                "Управление доступно только администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        rows = [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "📨 Отправить ежедневный отчёт", "callback_data": "control:daily"}],
            [{"text": "✅ Проверить работу системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]
        self.send(
            "🛠 <b>Управление</b>\n\nВыберите действие.",
            reply_markup=self.with_nav(rows),
        )

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if not data.startswith("bb:p:"):
            super().handle_callback(query)
            return

        query_id = str(query.get("id") or "")
        original_answer = self.answer
        participation_succeeded = False

        def capture_answer(callback_query_id: str, text: str) -> Any:
            nonlocal participation_succeeded
            if (
                str(callback_query_id) == query_id
                and str(text) in _PARTICIPATION_SUCCESS_ANSWERS
            ):
                participation_succeeded = True
            return original_answer(callback_query_id, text)

        self.answer = capture_answer  # type: ignore[method-assign]
        try:
            super().handle_callback(query)
        finally:
            self.answer = original_answer  # type: ignore[method-assign]

        if participation_succeeded:
            self._delete_callback_message(query)


def self_test() -> None:
    _runtime_self_test()

    original_handle_callback = TelegramPanelRuntime.handle_callback
    try:
        events: list[tuple[str, str]] = []
        panel = TelegramPanelRuntimeV41.__new__(TelegramPanelRuntimeV41)
        panel.answer = (  # type: ignore[method-assign]
            lambda query_id, text: events.append(("answer", str(text)))
        )
        panel._delete_callback_message = (  # type: ignore[method-assign]
            lambda query: events.append(("delete", str(query.get("data") or "")))
        )

        def successful_participation(self: TelegramPanelRuntime, query: dict[str, Any]) -> None:
            self.answer(str(query.get("id") or ""), "Ваше участие отмечено")

        TelegramPanelRuntime.handle_callback = successful_participation  # type: ignore[method-assign]
        panel.handle_callback({"id": "q1", "data": "bb:p:token"})
        assert ("delete", "bb:p:token") in events

        events.clear()

        def failed_participation(self: TelegramPanelRuntime, query: dict[str, Any]) -> None:
            self.answer(str(query.get("id") or ""), "Не удалось выполнить действие")

        TelegramPanelRuntime.handle_callback = failed_participation  # type: ignore[method-assign]
        panel.handle_callback({"id": "q2", "data": "bb:p:token"})
        assert not any(kind == "delete" for kind, _ in events)
    finally:
        TelegramPanelRuntime.handle_callback = original_handle_callback  # type: ignore[method-assign]

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
    assert "control:monitor" in callbacks

    print("BB V.G. v41 participation notification cleanup self-test passed")


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
