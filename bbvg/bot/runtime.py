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
        query_id = str(query.get("id") or "")

        if data.startswith(("bb:p:", "wheel:part:")):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            original_show_active = self.show_active
            self._edit_message_id = int(message.get("message_id") or 0) or None
            self.show_active = (  # type: ignore[method-assign]
                lambda page=0: self.show_menu(clear_stack=True)
            )
            try:
                super().handle_callback(query)
            finally:
                self.show_active = original_show_active  # type: ignore[method-assign]
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith(("bb:t:", "wheel:time:")):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                self._prepare_callback_user(query)
                self.answer(query_id, "Ручное указание времени отключено")
                self.show_menu(clear_stack=True)
            finally:
                self._edit_message_id = previous_edit_message_id
            return

        if data == "summary:send" or data.startswith("summary:send:") or data == "control:daily":
            self._prepare_callback_user(query)
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
        rows: list[list[dict[str, Any]]] = []
        for row in cleaned_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            filtered: list[dict[str, Any]] = []
            for button in row:
                if not isinstance(button, dict):
                    continue
                callback = str(button.get("callback_data") or "")
                if callback.startswith(("bb:t:", "wheel:time:")):
                    continue
                item = dict(button)
                label = str(item.get("text") or "")
                item["text"] = _BUTTON_COLOR_RE.sub("", label)
                filtered.append(item)
            if filtered:
                rows.append(filtered)
        cleaned_markup["inline_keyboard"] = rows
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
        rows = [
            [
                dict(button)
                for button in row
                if str(button.get("callback_data") or "") != "page:status"
            ]
            for row in self.compact_menu_rows(admin)
        ]
        rows = [row for row in rows if row]
        text = (
            "🎡 <b>BB V.G.</b>\n\n"
            "Находит колёса BetBoom, показывает время прокрутки и хранит отметки участия.\n\n"
            f"Ваша роль: <b>{html.escape(self.role_name(role))}</b>\n\n"
            "Выберите раздел."
        )
        self.send(text, reply_markup={"inline_keyboard": rows})

    def show_settings(self) -> None:
        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
            [{"text": "✅ Работа системы", "callback_data": "page:status"}],
        ]
        lines = [
            "⚙️ <b>Настройки</b>",
            "",
            "Личные настройки применяются только к вашему Telegram-аккаунту.",
        ]
        if self.is_admin():
            rows.extend(
                [
                    [{"text": "🧭 API и Legacy", "callback_data": "page:wheelmode"}],
                    [{"text": "⛔ Отключённый функционал", "callback_data": "page:disabled_features"}],
                ]
            )
            interval = int(
                self.load_access().get("settings", {}).get("monitor_interval_minutes", 5)
            )
            lines.extend(["", f"Интервал постоянной проверки: <b>{interval} мин.</b>"])
            rows.append([{"text": "⏱ Интервал проверки", "callback_data": "page:interval"}])
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
        else:
            rows.append([{"text": "🗑 Удалить мои данные", "callback_data": "privacy:delete:ask"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_disabled_features(self) -> None:
        text = (
            "⛔ <b>Отключённый функционал</b>\n\n"
            "• <b>Ручное указание времени</b> — скрыто и отключено: время берётся из "
            "BetBoom API, а без серверного времени действует штатное двухчасовое окно.\n"
            "• <b>Общее «Участвую»</b> — отключено: отметка всегда принадлежит только "
            "нажавшему пользователю.\n"
            "• <b>«Завершено» и «Неактивное»</b> — отключены: они конфликтуют с "
            "авторитетной BetBoom API-проверкой и могли удалить колесо раньше сервера.\n"
            "• <b>Скрытие или удаление пользователем</b> — отключено: общий список должен "
            "быть одинаковым, а личное участие хранится отдельно.\n"
            "• <b>Legacy HTML-checker</b> — не работает параллельно с API, чтобы одна ссылка "
            "не получала противоречивые статусы."
        )
        self.send(text, reply_markup=self.with_nav())

    def render_page(self, page: str) -> None:
        normalized = str(page or "")
        if normalized in {"wheelmode", "disabled_features"} and not self.is_admin():
            self.show_settings()
            return
        super().render_page(page)


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

    captured: list[tuple[str, dict[str, Any]]] = []
    panel = TelegramPanelRuntime()
    _configured_panel(panel, captured)
    panel.show_active()
    active_text, active_kwargs = captured[-1]
    active_markup = str(active_kwargs["reply_markup"])
    assert "@source, @second" in active_text
    assert "wheel:part:wheel-a" in active_markup
    assert "wheel:time:wheel-a" not in active_markup
    assert "wheel:finished:" not in active_markup
    assert "wheel:inactive:" not in active_markup

    cleaned_text, cleaned_markup = panel._simplify_active_payload(
        "<b>1. <code>wheel-a</code> 🔵</b>\n🔴 Время прокрутки неизвестно",
        {
            "inline_keyboard": [
                [{"text": "🔵 🎡 1 · Открыть колесо", "url": "https://example.com"}],
                [{"text": "🔵 ✅ 1 · Участвую", "callback_data": "join:wheel-a"}],
                [{"text": "⏱ 1 · Указать время", "callback_data": "wheel:time:wheel-a"}],
            ]
        },
    )
    assert "<code>wheel-a</code> 🔵" not in cleaned_text
    assert cleaned_markup is not None
    assert "wheel:time:" not in str(cleaned_markup)
    assert not telegram_ui.markup_issues(cleaned_markup)

    panel.navigation = {"1": ["menu"]}
    panel.role_for = lambda user_id: "admin"  # type: ignore[method-assign]
    panel.role_name = lambda role: "Администратор"  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.show_menu()
    assert "Ваша роль: <b>Администратор</b>" in captured[-1][0]
    panel.show_disabled_features()
    assert "Ручное указание времени" in captured[-1][0]

    callback_calls: list[tuple[str, Any]] = []
    panel._prepare_callback_user = lambda query: callback_calls.append(("prepare", query))  # type: ignore[method-assign]
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {"token": {"wheel_key": "wheel-a"}}}
    )
    panel.mark_personal_participation = lambda key: {"changed": True}  # type: ignore[method-assign]
    panel.answer = lambda query_id, text: callback_calls.append(("answer", text))  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: callback_calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "q",
            "data": "bb:p:token",
            "message": {"message_id": 77, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("menu", True) in callback_calls
    assert panel._edit_message_id is None

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
