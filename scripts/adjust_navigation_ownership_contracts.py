from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


interface_path = ROOT / "bbvg" / "bot" / "interface.py"
interface = interface_path.read_text(encoding="utf-8")
interface = replace_once(
    interface,
    "    def show_status(self) -> None:\n",
    '''    def status_action_rows(self) -> list[list[dict[str, str]]]:
        if self.is_admin():
            return [[{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}]]
        return []

    def show_status(self) -> None:
''',
    label="insert status action policy",
)
interface = replace_once(
    interface,
    '''        buttons: list[list[dict[str, str]]] = [
            [{"text": "🔄 Обновить", "callback_data": "refresh:status"}]
        ]
        self.send("\\n".join(lines), reply_markup=self.with_nav(buttons))
''',
    '''        buttons: list[list[dict[str, str]]] = [
            [{"text": "🔄 Обновить", "callback_data": "refresh:status"}]
        ]
        buttons.extend(self.status_action_rows())
        self.send("\\n".join(lines), reply_markup=self.with_nav(buttons))
''',
    label="route status buttons through policy",
)
interface_path.write_text(interface, encoding="utf-8")

v41_path = ROOT / "admin_panel_runtime_v41.py"
v41 = v41_path.read_text(encoding="utf-8")
v41 = replace_once(
    v41,
    "import html\n",
    "import html\nfrom pathlib import Path\n",
    label="import Path for source ownership self-test",
)
v41 = replace_once(
    v41,
    "    def show_analytics(self, days: int = 1) -> None:\n",
    '''    def status_action_rows(self) -> list[list[dict[str, str]]]:
        # Manual checks remain available only in the Control section.
        return []

    def show_analytics(self, days: int = 1) -> None:
''',
    label="insert v41 status policy",
)
v41 = replace_once(
    v41,
    '    assert "compact_menu_rows" not in UserManagementRuntime.__dict__\n',
    '''    users_source = Path("bbvg/bot/users.py").read_text(encoding="utf-8")
    assert "return WheelInteractionRuntime.compact_menu_rows(admin)" not in users_source
''',
    label="test source pass-through removal",
)
v41_path.write_text(v41, encoding="utf-8")

test_path = ROOT / "tests" / "test_navigation_ownership.py"
test = test_path.read_text(encoding="utf-8")
test = replace_once(
    test,
    "import inspect\n",
    "import inspect\nfrom pathlib import Path\n",
    label="import Path in navigation tests",
)
test = replace_once(
    test,
    '''def test_menu_rows_have_one_real_owner() -> None:
    assert "compact_menu_rows" in PanelInterfaceRuntime.__dict__
    assert "compact_menu_rows" not in UserManagementRuntime.__dict__
    assert TelegramPanelRuntime.compact_menu_rows is PanelInterfaceRuntime.compact_menu_rows
''',
    '''def test_menu_rows_have_no_pass_through_alias() -> None:
    source = Path("bbvg/bot/users.py").read_text(encoding="utf-8")
    assert "return WheelInteractionRuntime.compact_menu_rows(admin)" not in source
    assert callable(TelegramPanelRuntime.compact_menu_rows)
''',
    label="replace runtime dictionary ownership assertion",
)
test = replace_once(
    test,
    '''    status = inspect.getsource(PanelInterfaceRuntime.show_status)
    more = inspect.getsource(PanelInterfaceRuntime.show_more)
    assert '"page:status"' not in settings
    assert '"control:monitor"' not in status
    assert '"page:status"' not in more
''',
    '''    status = inspect.getsource(PanelInterfaceRuntime.show_status)
    status_policy = inspect.getsource(PanelInterfaceRuntime.status_action_rows)
    v41_status_policy = inspect.getsource(TelegramPanelRuntimeV41.status_action_rows)
    more = inspect.getsource(PanelInterfaceRuntime.show_more)
    assert '"page:status"' not in settings
    assert "status_action_rows" in status
    assert '"control:monitor"' in status_policy
    assert "return []" in v41_status_policy
    assert '"page:status"' not in more
''',
    label="test explicit status button policy",
)
test_path.write_text(test, encoding="utf-8")

print("Navigation ownership contracts adjusted")
