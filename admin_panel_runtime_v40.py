from __future__ import annotations

import argparse
import copy
import re
from types import SimpleNamespace
from typing import Any

import telegram_ui
from admin_panel_runtime_v39 import TelegramPanelRuntimeV39


RAINBOW_DOTS = ("🔵", "🟢", "🟡", "🟣", "🟠", "🔴")
_DOT_GROUP = "(?:" + "|".join(re.escape(value) for value in RAINBOW_DOTS) + ")"
_WHEEL_LINE_COLOR_RE = re.compile(
    rf"(?m)^(<b>\d+\. <code>.*?</code>)\s+{_DOT_GROUP}(</b>)$"
)
_BUTTON_COLOR_RE = re.compile(
    rf"^{_DOT_GROUP}\s+(?=(?:🎡|✅|🏁|🚫|⏱|🔄|🏠|\d))"
)
_BUTTON_INDEX_RE = re.compile(r"(?<!\d)(\d+)\s*·")


class TelegramPanelRuntimeV40(TelegramPanelRuntimeV39):
    """Keep numbered wheel controls clear and remove decorative rainbow dots."""

    RUNTIME_VERSION = 40

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
        """Compatibility alias: the former colorizer now removes rainbow markers."""
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


def _configured_panel(panel: TelegramPanelRuntimeV39, captured: list[tuple[str, dict[str, Any]]]) -> None:
    panel.current_user_id = "1"
    panel.current_role = "admin"
    panel.is_admin = lambda: True  # type: ignore[method-assign]
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
    panel._sources_for_item = lambda snap, key, item: ["source"]  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: [  # type: ignore[method-assign]
        {
            "_key": "wheel-a",
            "identifier": "wheel-a",
            "source": "source",
            "url": "https://betboom.ru/freestream/wheel-a",
        }
    ]
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]


def self_test() -> None:
    baseline_capture: list[tuple[str, dict[str, Any]]] = []
    simplified_capture: list[tuple[str, dict[str, Any]]] = []
    baseline = TelegramPanelRuntimeV39()
    simplified = TelegramPanelRuntimeV40()
    _configured_panel(baseline, baseline_capture)
    _configured_panel(simplified, simplified_capture)

    baseline.show_active()
    simplified.show_active()
    baseline_text, baseline_kwargs = baseline_capture[-1]
    simplified_text, simplified_kwargs = simplified_capture[-1]
    baseline_markup = baseline_kwargs["reply_markup"]
    simplified_markup = simplified_kwargs["reply_markup"]

    def callbacks(markup: dict[str, Any]) -> list[str]:
        return [
            str(button.get("callback_data") or "")
            for row in markup.get("inline_keyboard", [])
            for button in row
            if isinstance(button, dict) and button.get("callback_data")
        ]

    def urls(markup: dict[str, Any]) -> list[str]:
        return [
            str(button.get("url") or "")
            for row in markup.get("inline_keyboard", [])
            for button in row
            if isinstance(button, dict) and button.get("url")
        ]

    assert simplified_text == baseline_text
    assert callbacks(simplified_markup) == callbacks(baseline_markup)
    assert urls(simplified_markup) == urls(baseline_markup)

    rainbow_text = (
        "<b>1. <code>wheel-a</code> 🔵</b>\n"
        "🔴 Время прокрутки неизвестно"
    )
    rainbow_markup = {
        "inline_keyboard": [
            [{"text": "🔵 🎡 1 · Открыть колесо", "url": "https://example.com"}],
            [{"text": "🔵 ✅ 1 · Участвую", "callback_data": "join:wheel-a"}],
            [
                {"text": "🔵 🏁 1 · Завершено", "callback_data": "finish:wheel-a"},
                {"text": "🔵 🚫 1 · Неактивное", "callback_data": "inactive:wheel-a"},
            ],
        ]
    }
    cleaned_text, cleaned_markup = simplified._color_active_payload(
        rainbow_text, rainbow_markup
    )
    assert "<code>wheel-a</code> 🔵" not in cleaned_text
    assert "🔴 Время прокрутки неизвестно" in cleaned_text
    assert cleaned_markup is not None
    labels = [
        str(button.get("text") or "")
        for row in cleaned_markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict)
    ]
    assert labels == [
        "🎡 1 · Открыть колесо",
        "✅ 1 · Участвую",
        "🏁 1 · Завершено",
        "🚫 1 · Неактивное",
    ]
    assert callbacks(cleaned_markup) == callbacks(rainbow_markup)
    assert urls(cleaned_markup) == urls(rainbow_markup)
    assert not telegram_ui.markup_issues(cleaned_markup)
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(cleaned_markup))
    assert all(_BUTTON_INDEX_RE.search(label) for label in labels)
    print("BB V.G. v40 numbered controls without rainbow markers self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV40().run()


if __name__ == "__main__":
    raise SystemExit(main())
