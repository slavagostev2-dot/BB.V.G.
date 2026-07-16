from pathlib import Path

replacements = {
    "bbvg/bot/wheels.py": {
        "from admin_panel_runtime_v17 import TelegramPanelRuntimeV17\n": (
            "from bbvg.bot.source_requests import SourceRequestRuntime\n"
        ),
        "class WheelInteractionRuntime(TelegramPanelRuntimeV17):\n": (
            "class WheelInteractionRuntime(SourceRequestRuntime):\n"
        ),
    },
    "admin_panel_runtime_v25.py": {
        "from admin_panel_runtime_v17 import default_source_requests\n": (
            "from bbvg.bot.source_requests import default_source_requests\n"
        ),
        "from admin_panel_runtime_v21 import TelegramPanelRuntimeV21\n": (
            "from bbvg.bot.users import UserManagementRuntime\n"
        ),
        "TelegramPanelRuntimeV21.render_page(self, page)": (
            "UserManagementRuntime.render_page(self, page)"
        ),
    },
    "admin_panel_runtime_v26.py": {
        "from admin_panel_runtime_v17 import default_source_requests\n": (
            "from bbvg.bot.source_requests import default_source_requests\n"
        ),
    },
    "admin_panel_runtime_v30.py": {
        "from admin_panel_runtime_v17 import default_source_requests\n": (
            "from bbvg.bot.source_requests import default_source_requests\n"
        ),
        "from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS, USER_NOTIFICATION_OPTIONS\n": (
            "from bbvg.bot.users import ADMIN_NOTIFICATION_OPTIONS, USER_NOTIFICATION_OPTIONS\n"
        ),
    },
    "admin_panel_runtime_v37.py": {
        "from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS\n": (
            "from bbvg.bot.users import ADMIN_NOTIFICATION_OPTIONS\n"
        ),
    },
    "system_checks.py": {
        '    ROOT / "admin_panel_runtime_v17.py",\n': (
            '    ROOT / "bbvg" / "bot" / "source_requests.py",\n'
        ),
        '    ROOT / "admin_panel_runtime_v21.py",\n': (
            '    ROOT / "bbvg" / "bot" / "users.py",\n'
        ),
    },
    "tests/test_nightly_idle_policy.py": {
        "from admin_panel_runtime_v17 import TelegramPanelRuntimeV17\n": (
            "from bbvg.bot.source_requests import SourceRequestRuntime\n"
        ),
        "            TelegramPanelRuntimeV17.decide_source_request,\n": (
            "            SourceRequestRuntime.decide_source_request,\n"
        ),
    },
}

for path_text, mapping in replacements.items():
    path = Path(path_text)
    text = path.read_text(encoding="utf-8")
    for old, new in mapping.items():
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"{path_text}: expected one occurrence of {old!r}, found {count}"
            )
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
