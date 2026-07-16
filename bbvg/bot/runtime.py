from __future__ import annotations

import argparse
import copy
import html
import os
import re
from types import SimpleNamespace
from typing import Any

import personal_wheel_voting
import telegram_ui
from admin_panel_runtime_v31 import SUMMARY_PERIODS
from admin_panel_runtime_v38 import TelegramPanelRuntimeV38


def _install_dedicated_vote_key_contract() -> None:
    """Prevent BOT_TOKEN from becoming a pseudonym key in the live panel."""

    if getattr(personal_wheel_voting, "_bbvg_dedicated_vote_key_installed", False):
        return
    original = personal_wheel_voting.actor_vote_token

    def dedicated_actor_vote_token(user_id: str, secret: str | None = None) -> str:
        dedicated = str(secret or os.getenv("BOT_STATE_KEY") or "").strip()
        if not dedicated:
            raise RuntimeError("BOT_STATE_KEY is required for personal vote pseudonyms")
        return original(user_id, secret=dedicated)

    personal_wheel_voting.actor_vote_token = dedicated_actor_vote_token
    personal_wheel_voting._bbvg_dedicated_vote_key_installed = True


_install_dedicated_vote_key_contract()
PersonalWheelVotingMixin = personal_wheel_voting.PersonalWheelVotingMixin

RAINBOW_DOTS = ("🔵", "🟢", "🟡", "🟣", "🟠", "🔴")
_DOT_GROUP = "(?:" + "|".join(re.escape(value) for value in RAINBOW_DOTS) + ")"
_WHEEL_LINE_COLOR_RE = re.compile(
    rf"(?m)^(<b>\d+\. <code>.*?</code>)\s+{_DOT_GROUP}(</b>)$"
)
_BUTTON_COLOR_RE = re.compile(
    rf"^{_DOT_GROUP}\s+(?=(?:🎡|✅|🏁|🚫|⏱|🔄|🏠|\d))"
)
_BUTTON_INDEX_RE = re.compile(r"(?<!\d)(\d+)\s*·")


class TelegramPanelRuntime(PersonalWheelVotingMixin, TelegramPanelRuntimeV38):
    """Current Telegram control center without version-layer inheritance."""

    RUNTIME_VERSION = 41

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data == "summary:send" or data.startswith("summary:send:") or data == "control:daily":
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            if not self.is_admin():
                self.answer(query_id, "Недоступно")
                return
            if data == "summary:send":
                self.answer(query_id, "Выберите период")
                self.show_send_summary_menu()
                return

            period = "daily" if data == "control:daily" else data.rsplit(":", 1)[1]
            if period not in SUMMARY_PERIODS:
                self.answer(query_id, "Неизвестный период")
                return

            days, label = SUMMARY_PERIODS[period]
            self.answer(query_id, "Сводка сформирована")
            self.send(
                f"📨 <b>{html.escape(label)} сводка</b>\n\n"
                "Сводка сформирована непосредственно ботом без отдельного технического запуска.",
                reply_markup=self.with_nav(),
            )
            self.show_period_report(days)
            return
        super().handle_callback(query)

    @classmethod
    def _simplify_active_payload(
        cls,
        text: str,
        reply_markup: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        cleaned_text = _WHEEL_LINE_COLOR_RE.sub(r"\1\2", str(text or ""))
        if not isinstance(reply_markup, dict):
            return cleaned_text, reply_markup

        cleaned_markup = copy.deepcopy(reply_markup)
        for row in cleaned_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                label = str(button.get("text") or "")
                cleaned = _BUTTON_COLOR_RE.sub("", label)
                if cleaned != label:
                    button["text"] = cleaned
        return cleaned_text, cleaned_markup

    @classmethod
    def _color_active_payload(
        cls,
        text: str,
        reply_markup: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Compatibility alias retained while old tests and workflows are migrated."""
        return cls._simplify_active_payload(text, reply_markup)

    def show_active(self, page: int = 0) -> None:
        original_send = self.send

        def simplified_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            cleaned_text, cleaned_markup = self._simplify_active_payload(
                text, reply_markup
            )
            return original_send(
                cleaned_text,
                reply_markup=cleaned_markup,
                chat_id=chat_id,
            )

        self.send = simplified_send  # type: ignore[method-assign]
        try:
            super().show_active(page)
        finally:
            self.send = original_send  # type: ignore[method-assign]

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        admin = role in {"owner", "admin"}
        text = (
            "🎡 <b>BB V.G.</b>\n\n"
            "Находит колёса BetBoom, показывает время прокрутки и хранит отметки участия.\n\n"
            f"Ваша роль: <b>{html.escape(self.role_name(role))}</b>\n\n"
            "Выберите раздел."
        )
        self.send(
            text,
            reply_markup={"inline_keyboard": self.compact_menu_rows(admin)},
        )


def _configured_panel(
    panel: TelegramPanelRuntimeV38,
    captured: list[tuple[str, dict[str, Any]]],
) -> None:
    panel.current_user_id = "1"
    panel.current_chat_id = "1"
    panel.current_role = "admin"
    panel.is_admin = lambda: True  # type: ignore[method-assign]
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        state={"active_wheels": {}},
        stats={"sources": {}, "daily": {}},
        health={"sources": {}},
        discovery={"sources": {}},
        fast=[],
        nightly=[],
    )
    panel._monitor_status = lambda: {}  # type: ignore[method-assign]
    panel._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
    panel._personal_participating_wheels = lambda: set()  # type: ignore[method-assign]
    panel._sources_for_item = lambda snap, key, item: ["source", "second"]  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
        {
            "_key": "wheel-a",
            "identifier": "wheel-a",
            "source": "source",
            "sources": ["source", "second"],
            "action_id": 101,
            "url": "https://betboom.ru/freestream/wheel-a",
        }
    ]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]


def self_test() -> None:
    assert TelegramPanelRuntime.RUNTIME_VERSION == 41
    assert issubclass(TelegramPanelRuntime, TelegramPanelRuntimeV38)
    assert issubclass(TelegramPanelRuntime, PersonalWheelVotingMixin)
    assert SUMMARY_PERIODS["daily"][0] == 1
    assert SUMMARY_PERIODS["weekly"][0] == 7
    assert SUMMARY_PERIODS["monthly"][0] == 30

    current_capture: list[tuple[str, dict[str, Any]]] = []
    current = TelegramPanelRuntime()
    _configured_panel(current, current_capture)
    current.show_active()
    active_text, active_kwargs = current_capture[-1]
    assert "@source, @second" in active_text
    assert "Участие не отмечено" in active_text
    active_markup = str(active_kwargs["reply_markup"])
    assert "wheel:part:wheel-a" in active_markup
    assert "wheel:time:wheel-a" in active_markup
    assert "wheel:finished:" not in active_markup
    assert "wheel:inactive:" not in active_markup

    rainbow_text = "<b>1. <code>wheel-a</code> 🔵</b>\n🔴 Время прокрутки неизвестно"
    rainbow_markup = {
        "inline_keyboard": [
            [{"text": "🔵 🎡 1 · Открыть колесо", "url": "https://example.com"}],
            [{"text": "🔵 ✅ 1 · Участвую", "callback_data": "join:wheel-a"}],
        ]
    }
    cleaned_text, cleaned_markup = current._simplify_active_payload(
        rainbow_text, rainbow_markup
    )
    assert "<code>wheel-a</code> 🔵" not in cleaned_text
    assert "🔴 Время прокрутки неизвестно" in cleaned_text
    assert cleaned_markup is not None
    assert not telegram_ui.markup_issues(cleaned_markup)
    labels = [
        str(button.get("text") or "")
        for row in cleaned_markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict)
    ]
    assert labels == ["🎡 1 · Открыть колесо", "✅ 1 · Участвую"]
    callbacks = [
        str(button.get("callback_data") or "")
        for row in cleaned_markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict) and button.get("callback_data")
    ]
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
    assert all(_BUTTON_INDEX_RE.search(label) for label in labels)

    menu_capture: list[tuple[str, dict[str, Any]]] = []
    current.current_user_id = "1"
    current.current_role = "admin"
    current.navigation = {"1": ["menu"]}
    current.role_for = lambda user_id: "admin"  # type: ignore[method-assign]
    current.role_name = lambda role: "Администратор"  # type: ignore[method-assign]
    current.send = lambda text, **kwargs: menu_capture.append((text, kwargs)) or {}  # type: ignore[method-assign]
    current.show_menu()
    menu_text, menu_kwargs = menu_capture[-1]
    assert "Находит колёса BetBoom" in menu_text
    assert "Ваша роль: <b>Администратор</b>" in menu_text
    menu_callbacks = [
        str(button.get("callback_data") or "")
        for row in menu_kwargs["reply_markup"]["inline_keyboard"]
        for button in row
        if isinstance(button, dict)
    ]
    assert "page:active" in menu_callbacks
    assert "page:control" in menu_callbacks

    summary_calls: list[tuple[str, Any]] = []
    current._prepare_callback_user = lambda query: summary_calls.append(("prepare", query))  # type: ignore[method-assign]
    current.is_admin = lambda: True  # type: ignore[method-assign]
    current.answer = lambda query_id, text: summary_calls.append(("answer", (query_id, text)))  # type: ignore[method-assign]
    current.send = lambda text, **kwargs: summary_calls.append(("send", (text, kwargs))) or {}  # type: ignore[method-assign]
    current.with_nav = lambda: {"inline_keyboard": []}  # type: ignore[method-assign]
    current.show_period_report = lambda days: summary_calls.append(("report", days))  # type: ignore[method-assign]
    current.handle_callback({"id": "q1", "data": "summary:send:weekly"})
    assert ("report", 7) in summary_calls
    assert any(name == "send" for name, _ in summary_calls)

    print("BB V.G. consolidated Telegram panel runtime self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntime().run()


if __name__ == "__main__":
    raise SystemExit(main())
