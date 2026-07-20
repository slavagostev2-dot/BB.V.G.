from __future__ import annotations

import inspect

import admin_panel_runtime_v41
from bbvg.bot.control_center import TelegramPanelRuntimeV41 as StableControlCenterRuntime


def callback_rows(admin: bool) -> list[list[str]]:
    return [
        [str(button.get("callback_data") or "") for button in row]
        for row in StableControlCenterRuntime.compact_menu_rows(admin)
    ]


def test_v41_entrypoint_reexports_stable_control_center_runtime() -> None:
    runtime = admin_panel_runtime_v41.TelegramPanelRuntimeV41
    assert runtime is StableControlCenterRuntime
    assert runtime.__module__ == "bbvg.bot.control_center"
    wrapper_source = inspect.getsource(admin_panel_runtime_v41)
    assert "def show_" not in wrapper_source
    assert "def handle_callback" not in wrapper_source


def test_user_menu_order_is_frozen() -> None:
    assert callback_rows(False) == [
        ["page:active", "page:analytics"],
        ["page:sources", "page:settings"],
        ["page:status"],
        ["page:profile"],
    ]


def test_admin_menu_order_is_frozen() -> None:
    assert callback_rows(True) == [
        ["page:active", "page:analytics"],
        ["page:sources", "page:settings"],
        ["page:control"],
        ["page:profile"],
    ]
