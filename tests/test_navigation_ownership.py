from __future__ import annotations

import inspect
from pathlib import Path

from admin_panel_runtime_v41 import TelegramPanelRuntimeV41, self_test
from bbvg.bot.interface import PanelInterfaceRuntime
from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.users import UserManagementRuntime


def test_navigation_ownership_self_test() -> None:
    self_test()


def test_menu_rows_have_no_pass_through_alias() -> None:
    source = Path("bbvg/bot/users.py").read_text(encoding="utf-8")
    assert "return WheelInteractionRuntime.compact_menu_rows(admin)" not in source
    assert callable(TelegramPanelRuntime.compact_menu_rows)


def test_v41_does_not_filter_rendered_keyboards() -> None:
    for name in (
        "_without_callbacks",
        "_render_with_filtered_callbacks",
        "show_settings",
        "show_status",
        "show_more",
    ):
        assert name not in TelegramPanelRuntimeV41.__dict__
    source = inspect.getsource(TelegramPanelRuntimeV41.show_control)
    assert "control_menu_rows" in source


def test_final_screen_owners_do_not_emit_callbacks_removed_by_v41() -> None:
    settings = inspect.getsource(TelegramPanelRuntime.show_settings)
    status = inspect.getsource(PanelInterfaceRuntime.show_status)
    status_policy = inspect.getsource(PanelInterfaceRuntime.status_action_rows)
    v41_status_policy = inspect.getsource(TelegramPanelRuntimeV41.status_action_rows)
    more = inspect.getsource(PanelInterfaceRuntime.show_more)
    assert '"page:status"' not in settings
    assert "status_action_rows" in status
    assert '"control:monitor"' in status_policy
    assert "return []" in v41_status_policy
    assert '"page:status"' not in more
