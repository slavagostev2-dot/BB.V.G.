from __future__ import annotations

import argparse
import copy
import re
from types import SimpleNamespace
from typing import Any

import telegram_ui
from admin_panel_runtime_v39 import TelegramPanelRuntimeV39


WHEEL_COLORS = ("🔵", "🟢", "🟡", "🟣", "🟠", "🔴")
_WHEEL_LINE_RE = re.compile(r"(?m)^<b>(\d+)\. (<code>.*?</code>)</b>$")
_BUTTON_INDEX_RE = re.compile(r"(?<!\d)(\d+)\s*·")


class TelegramPanelRuntimeV40(TelegramPanelRuntimeV39):
    """Add visual wheel linkage without changing any callback payload."""

    RUNTIME_VERSION = 40

    @staticmethod
    def wheel_color(index: int) -> str:
        return WHEEL_COLORS[(max(1, int(index)) - 1) % len(WHEEL_COLORS)]

    @classmethod
    def _color_active_payload(
        cls,
        text: str,
        reply_markup: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        def line_replacement(match: re.Match[str]) -> str:
            index = int(match.group(1))
            return f"<b>{index}. {match.group(2)} {cls.wheel_color(index)}</b>"

        colored_text = _WHEEL_LINE_RE.sub(line_replacement, str(text or ""))
        if not isinstance(reply_markup, dict):
            return colored_text, reply_markup

        colored_markup = copy.deepcopy(reply_markup)
        for row in colored_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                label = str(button.get("text") or "")
                match = _BUTTON_INDEX_RE.search(label)
                if match is None:
                    continue
                index = int(match.group(1))
                color = cls.wheel_color(index)
                if not label.startswith(color):
                    button["text"] = f"{color} {label}"
        return colored_text, colored_markup

    def show_active(self, page: int = 0) -> None:
        original_send = self.send

        def colored_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            colored_text, colored_markup = self._color_active_payload(text, reply_markup)
            return original_send(
                colored_text,
                reply_markup=colored_markup,
                chat_id=chat_id,
            )

        self.send = colored_send  # type: ignore[method-assign]
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
    colored_capture: list[tuple[str, dict[str, Any]]] = []
    baseline = TelegramPanelRuntimeV39()
    colored = TelegramPanelRuntimeV40()
    _configured_panel(baseline, baseline_capture)
    _configured_panel(colored, colored_capture)

    baseline.show_active()
    colored.show_active()
    baseline_text, baseline_kwargs = baseline_capture[-1]
    colored_text, colored_kwargs = colored_capture[-1]
    baseline_markup = baseline_kwargs["reply_markup"]
    colored_markup = colored_kwargs["reply_markup"]

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

    assert baseline_text != colored_text
    assert "1. <code>wheel-a</code> 🔵" in colored_text
    assert callbacks(colored_markup) == callbacks(baseline_markup)
    assert urls(colored_markup) == urls(baseline_markup)
    wheel_labels = [
        str(button.get("text") or "")
        for row in colored_markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict) and _BUTTON_INDEX_RE.search(str(button.get("text") or ""))
    ]
    assert wheel_labels and all(label.startswith("🔵 ") for label in wheel_labels)
    assert not telegram_ui.markup_issues(colored_markup)
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(colored_markup))
    print("BB V.G. v40 callback-safe color interface self-test passed")


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
