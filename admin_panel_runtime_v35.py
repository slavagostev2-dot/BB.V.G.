from __future__ import annotations

import argparse

from admin_panel_runtime_v34 import TelegramPanelRuntimeV34
from bbvg.bot.storage import self_test as storage_self_test
from bbvg.bot.users import self_test as users_self_test


class TelegramPanelRuntimeV35(TelegramPanelRuntimeV34):
    """Compatibility entrypoint after user settings and storage consolidation."""


def self_test() -> None:
    storage_self_test()
    users_self_test()
    panel = TelegramPanelRuntimeV35()
    assert hasattr(panel, "set_user_notification")
    assert hasattr(panel, "set_all_user_notifications")
    print("admin_panel_runtime_v35 compatibility self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV35().run()


if __name__ == "__main__":
    raise SystemExit(main())
