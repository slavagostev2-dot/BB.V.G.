from __future__ import annotations

import argparse
import html
import re
from types import SimpleNamespace
from typing import Any

from admin_bot import COMMANDS
from admin_panel_runtime_v35 import TelegramPanelRuntimeV35

TECHNICAL_ERROR_RE = re.compile(
    r"(?:Не удалось выполнить команду:\s*<code>[^<]+</code>|"
    r"Диагностика не выполнена:\s*(?:<code>)?[^<.\n]+(?:</code>)?|"
    r"Traceback \(most recent call last\))",
    re.IGNORECASE,
)
USER_ACTION_ERROR = (
    "⚠️ <b>Не удалось выполнить действие.</b>\n\n"
    "Попробуйте ещё раз или вернитесь в главное меню."
)


class TelegramPanelRuntimeV36(TelegramPanelRuntimeV35):
    """Chapter 2: durable notification semantics and one source-rating name."""

    @staticmethod
    def safe_text_for_role(text: str, role: str) -> str:
        value = str(text or "")
        if role not in {"owner", "admin"} and TECHNICAL_ERROR_RE.search(value):
            return USER_ACTION_ERROR
        return value

    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        value = self.safe_text_for_role(text, self.current_role)
        return super().send(value, reply_markup=reply_markup, chat_id=chat_id)

    def setup_bot(self) -> None:
        super().setup_bot()
        commands = [dict(item) for item in COMMANDS]
        for item in commands:
            if item.get("command") == "ranking":
                item["description"] = "Рейтинг источников"
            elif item.get("command") == "reports":
                item["description"] = "Сводки"
        self.telegram_api("setMyCommands", {"commands": commands})

    @staticmethod
    def source_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = TelegramPanelRuntimeV35.source_menu_rows(admin)
        for row in rows:
            for button in row:
                if button.get("callback_data") == "page:ranking":
                    button["text"] = "🏆 Рейтинг источников"
        return rows

    def show_ranking(self) -> None:
        rows = self.ranked_sources(self.snapshot().stats)
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            "Рейтинг формируется только по решениям администратора.",
            "Подтверждённое колесо даёт источнику +40 очков; "
            "отметка «Неактивное» очки не уменьшает.",
            "",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score, confirmed) in enumerate(rows, 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(
                f"{mark} <b>@{html.escape(source)}</b> — <b>{score}</b> оч. "
                f"({confirmed} подтвержд.)"
            )
        if not rows:
            lines.append("Пока нет источников с положительным рейтингом.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить рейтинг", "callback_data": "page:ranking"}]]
            ),
        )


def self_test() -> None:
    panel = TelegramPanelRuntimeV36()
    assert panel.safe_text_for_role(
        "⚠️ Не удалось выполнить команду: <code>ValueError</code>.", "user"
    ) == USER_ACTION_ERROR
    assert "ValueError" in panel.safe_text_for_role(
        "⚠️ Не удалось выполнить команду: <code>ValueError</code>.", "admin"
    )

    user_rows = panel.source_menu_rows(False)
    ranking_buttons = [
        button
        for row in user_rows
        for button in row
        if button.get("callback_data") == "page:ranking"
    ]
    assert ranking_buttons and ranking_buttons[0]["text"] == "🏆 Рейтинг источников"

    captured: list[str] = []
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        stats={
            "sources": {
                "sourceA": {"quality_score": 80, "admin_confirmed_wheels": 2},
                "sourceB": {"quality_score": 0, "admin_confirmed_wheels": 0},
            }
        }
    )
    panel.send = lambda text, **kwargs: captured.append(text) or {"ok": True}  # type: ignore[method-assign]
    panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
    panel.show_ranking()
    assert captured and "Рейтинг источников" in captured[0]
    assert "@sourceA" in captured[0]
    assert "@sourceB" not in captured[0]
    print("admin panel v36 chapter 2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV36().run()


if __name__ == "__main__":
    raise SystemExit(main())
