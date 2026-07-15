from __future__ import annotations

from pathlib import Path

import telegram_ui
from admin_panel_runtime_v38 import TelegramPanelRuntimeV38, self_test as panel_self_test


ROOT = Path(__file__).resolve().parent


def main() -> int:
    telegram_ui.self_test()
    panel_self_test()

    workflow = (ROOT / ".github/workflows/admin-bot.yml").read_text(encoding="utf-8")
    assert "run: python admin_panel_runtime_v38.py" in workflow
    assert '"version": 38' in workflow
    assert "admin_panel_runtime_v38.py" in workflow
    assert "telegram_ui.py" in workflow

    user_callbacks = {
        str(button.get("callback_data") or "")
        for row in TelegramPanelRuntimeV38.compact_menu_rows(False)
        for button in row
    }
    admin_callbacks = {
        str(button.get("callback_data") or "")
        for row in TelegramPanelRuntimeV38.compact_menu_rows(True)
        for button in row
    }
    assert "page:status" in user_callbacks
    assert "page:control" not in user_callbacks
    assert "page:control" in admin_callbacks
    assert "page:status" not in admin_callbacks
    assert not telegram_ui.markup_issues(
        {"inline_keyboard": TelegramPanelRuntimeV38.compact_menu_rows(False)}
    )
    assert not telegram_ui.markup_issues(
        {"inline_keyboard": TelegramPanelRuntimeV38.compact_menu_rows(True)}
    )

    archived = (ROOT / "MINI_APP_ARCHIVED.md").read_text(encoding="utf-8")
    assert "Mini App — архивировано" in archived
    assert (ROOT / "tests/test_ui_chapter4.py").exists()
    print("Chapter 4 interface and analytics acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
