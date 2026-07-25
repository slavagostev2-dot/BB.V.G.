from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


users_path = ROOT / "bbvg" / "bot" / "users.py"
users = users_path.read_text(encoding="utf-8")
users = replace_once(
    users,
    '''    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        return WheelInteractionRuntime.compact_menu_rows(admin)

''',
    "",
    label="remove compact menu pass-through alias",
)
users_path.write_text(users, encoding="utf-8")

runtime_path = ROOT / "bbvg" / "bot" / "runtime.py"
runtime = runtime_path.read_text(encoding="utf-8")
runtime = replace_once(
    runtime,
    '''        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
            [{"text": "✅ Работа системы", "callback_data": "page:status"}],
        ]
''',
    '''        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
        ]
''',
    label="settings owns final callbacks",
)
runtime_path.write_text(runtime, encoding="utf-8")

interface_path = ROOT / "bbvg" / "bot" / "interface.py"
interface = interface_path.read_text(encoding="utf-8")
interface = replace_once(
    interface,
    '''                [
                    [
                        {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                        {
                            "text": "✅ Состояние системы",
                            "callback_data": "page:status",
                        },
                    ],
                ]
''',
    '''                [
                    [{"text": "⚙️ Настройки", "callback_data": "page:settings"}],
                ]
''',
    label="more page owns final callbacks",
)
interface = replace_once(
    interface,
    '''        if self.is_admin():
            buttons.append([{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}])
        self.send("\\n".join(lines), reply_markup=self.with_nav(buttons))
''',
    '''        self.send("\\n".join(lines), reply_markup=self.with_nav(buttons))
''',
    label="status page owns final callbacks",
)
interface_path.write_text(interface, encoding="utf-8")

v41_path = ROOT / "admin_panel_runtime_v41.py"
v41 = v41_path.read_text(encoding="utf-8")
v41 = replace_once(v41, "from typing import Any, Callable\n", "from typing import Any\n", label="remove Callable import")
start = v41.index("    @staticmethod\n    def _without_callbacks(")
end = v41.index("    def show_control(self) -> None:\n", start)
v41 = v41[:start] + v41[end:]
v41 = replace_once(
    v41,
    '''        rows = [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "✅ Проверить работу системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]
''',
    "        rows = self.control_menu_rows()\n",
    label="control page uses canonical menu rows",
)
for name, next_name in (
    ("show_settings", "show_status"),
    ("show_status", "show_more"),
    ("show_more", "show_analytics"),
):
    start = v41.index(f"    def {name}(self)")
    end = v41.index(f"    def {next_name}(", start)
    v41 = v41[:start] + v41[end:]

old_assertion_start = v41.index("    assert TelegramPanelRuntimeV41._without_callbacks(")
old_assertion_end = v41.index("\n\n    access = {", old_assertion_start)
new_assertions = '''    assert "compact_menu_rows" not in UserManagementRuntime.__dict__
    for name in (
        "_without_callbacks",
        "_render_with_filtered_callbacks",
        "show_settings",
        "show_status",
        "show_more",
    ):
        assert name not in TelegramPanelRuntimeV41.__dict__

    captured.clear()
    panel = TelegramPanelRuntimeV41.__new__(TelegramPanelRuntimeV41)
    panel.current_user_id = "1"
    panel.current_chat_id = "1"
    panel.current_role = "owner"
    panel.navigation = {}
    panel.is_admin = lambda: True  # type: ignore[method-assign]
    panel.is_owner = lambda: True  # type: ignore[method-assign]
    panel.role_for = lambda user_id=None: "owner"  # type: ignore[method-assign]
    panel.load_access = lambda force=False: {  # type: ignore[method-assign]
        "settings": {"monitor_interval_minutes": 5},
        "users": {},
        "owner_id": "1",
        "admins": [],
    }
    panel.send = lambda text, **kwargs: captured.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.show_settings()
    settings_callbacks = {
        str(button.get("callback_data") or "")
        for row in captured[-1][1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert "page:notifications" in settings_callbacks
    assert "page:status" not in settings_callbacks

    captured.clear()
    panel.show_more()
    more_callbacks = {
        str(button.get("callback_data") or "")
        for row in captured[-1][1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert "page:settings" in more_callbacks
    assert "page:status" not in more_callbacks

    captured.clear()
    panel.snapshot = lambda force=False: type(  # type: ignore[method-assign]
        "Snap",
        (),
        {"fast": ["source"], "nightly": [], "state": {}, "stats": {}, "health": {}},
    )()
    panel._monitor_status = lambda: {  # type: ignore[method-assign]
        "checked_sources": 1,
        "reachable_sources": 1,
        "source_errors": 0,
        "last_successful_iteration_at": "2026-07-25T00:00:00+00:00",
    }
    panel.load_source_registry = lambda: {"summary": {"total": 1}}  # type: ignore[method-assign]
    panel._collect_current_wheels = lambda: []  # type: ignore[method-assign]
    panel.show_status()
    status_callbacks = {
        str(button.get("callback_data") or "")
        for row in captured[-1][1]["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert "refresh:status" in status_callbacks
    assert "control:monitor" not in status_callbacks
'''
v41 = v41[:old_assertion_start] + new_assertions + v41[old_assertion_end:]
v41 = replace_once(
    v41,
    "from bbvg.bot.runtime import self_test as _runtime_self_test\n",
    "from bbvg.bot.runtime import self_test as _runtime_self_test\nfrom bbvg.bot.users import UserManagementRuntime\n",
    label="self-test ownership import",
)
v41_path.write_text(v41, encoding="utf-8")

test_path = ROOT / "tests" / "test_navigation_ownership.py"
test_path.write_text('''from __future__ import annotations

import inspect

from admin_panel_runtime_v41 import TelegramPanelRuntimeV41, self_test
from bbvg.bot.interface import PanelInterfaceRuntime
from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.users import UserManagementRuntime


def test_navigation_ownership_self_test() -> None:
    self_test()


def test_menu_rows_have_one_real_owner() -> None:
    assert "compact_menu_rows" in PanelInterfaceRuntime.__dict__
    assert "compact_menu_rows" not in UserManagementRuntime.__dict__
    assert TelegramPanelRuntime.compact_menu_rows is PanelInterfaceRuntime.compact_menu_rows


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
    more = inspect.getsource(PanelInterfaceRuntime.show_more)
    assert '"page:status"' not in settings
    assert '"control:monitor"' not in status
    assert '"page:status"' not in more
''', encoding="utf-8")

changelog_path = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
changelog = changelog_path.read_text(encoding="utf-8")
marker = "### Единый владелец кнопок навигации Control Center"
if marker not in changelog:
    changelog += '''\n\n### Единый владелец кнопок навигации Control Center\n\n- экраны настроек, статуса и дополнительных разделов теперь сразу формируют окончательный набор кнопок;\n- из `admin_panel_runtime_v41.py` удалены три перехвата, временно подменявшие `self.send` для фильтрации callback;\n- `compact_menu_rows` остаётся только у `PanelInterfaceRuntime`, pass-through-алиас удалён;\n- раздел «Управление» использует канонический `control_menu_rows`;\n- строки callback и пользовательская навигация не изменены.\n'''
changelog_path.write_text(changelog, encoding="utf-8")

plan_path = ROOT / "engineering" / "REFACTOR_PLAN_RU.md"
plan = plan_path.read_text(encoding="utf-8")
plan_marker = "- кнопки навигации настроек, статуса и дополнительных разделов"
if plan_marker not in plan:
    plan += '''\n\n## Выполнено 25 июля 2026 года — консолидация навигации\n\n- кнопки навигации настроек, статуса и дополнительных разделов формируются предметными владельцами без post-render фильтрации;\n- `admin_panel_runtime_v41.py` больше не подменяет `self.send` для удаления callback;\n- построение компактного меню закреплено за `PanelInterfaceRuntime`;\n- следующий этап не должен затрагивать личное участие и защищённую загрузку snapshot.\n'''
plan_path.write_text(plan, encoding="utf-8")

print("Control Center navigation ownership patch prepared")
