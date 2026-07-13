from __future__ import annotations

import argparse
from typing import Any

from admin_panel_runtime_v11 import TelegramPanelRuntimeV11


class TelegramPanelRuntimeV12(TelegramPanelRuntimeV11):
    """Panel v12: context-aware navigation without duplicate destinations."""

    @staticmethod
    def _callback_page(data: str) -> str | None:
        if data.startswith("page:"):
            return data[5:]
        return None

    def nav_rows(self) -> list[list[dict[str, str]]]:
        stack = self.stack()
        previous = stack[-2] if len(stack) >= 2 else None

        # On a first-level screen, Back and Home both lead to the main menu.
        # Show only one button instead of two identical actions.
        if previous in {None, "menu"}:
            return [[{"text": "🏠 Главное меню", "callback_data": "nav:home"}]]

        return [[
            {"text": "⬅️ Назад", "callback_data": "nav:back"},
            {"text": "🏠 Главное меню", "callback_data": "nav:home"},
        ]]

    def with_nav(
        self,
        rows: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        stack = self.stack()
        previous = stack[-2] if len(stack) >= 2 else None
        cleaned: list[list[dict[str, str]]] = []
        seen: set[tuple[str, str]] = set()

        for row in rows or []:
            kept: list[dict[str, str]] = []
            for button in row:
                data = str(button.get("callback_data") or "")
                target = self._callback_page(data)

                # Generic navigation already covers these destinations.
                if data in {"nav:back", "nav:home"}:
                    continue
                if target == "menu":
                    continue
                if previous and target == previous:
                    continue

                if data:
                    identity = ("callback", data)
                elif button.get("url"):
                    identity = ("url", str(button.get("url")))
                elif button.get("web_app"):
                    identity = ("web_app", str(button.get("web_app")))
                else:
                    identity = ("text", str(button.get("text") or ""))

                if identity in seen:
                    continue
                seen.add(identity)
                kept.append(button)

            if kept:
                cleaned.append(kept)

        return {"inline_keyboard": cleaned + self.nav_rows()}


class _NavigationTestPanel(TelegramPanelRuntimeV12):
    def __init__(self) -> None:
        self.current_user_id = "1"
        self.navigation = {"1": ["menu"]}


def self_test() -> None:
    panel = _NavigationTestPanel()

    panel.navigation["1"] = ["menu", "discovery"]
    first_level = panel.with_nav([])["inline_keyboard"]
    assert first_level == [[{"text": "🏠 Главное меню", "callback_data": "nav:home"}]]

    panel.navigation["1"] = ["menu", "discovery", "candidate:list:nightly:0"]
    nested = panel.with_nav([
        [{"text": "🌙 К сводке ночного наблюдения", "callback_data": "page:discovery"}],
        [{"text": "@channel", "callback_data": "candidate:detail:channel"}],
        [{"text": "@channel duplicate", "callback_data": "candidate:detail:channel"}],
    ])["inline_keyboard"]

    flat = [button for row in nested for button in row]
    callbacks = [button.get("callback_data") for button in flat]
    assert "page:discovery" not in callbacks
    assert callbacks.count("candidate:detail:channel") == 1
    assert "nav:back" in callbacks and "nav:home" in callbacks

    print("admin_panel_runtime_v12 navigation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV12().run()


if __name__ == "__main__":
    raise SystemExit(main())
