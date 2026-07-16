from __future__ import annotations

import argparse

from admin_panel_runtime_v33 import TelegramPanelRuntimeV33
from bbvg.bot.storage import _clone, _merge_set_list, _merge_value
from bbvg.bot.users import (
    ALL_SUMMARY_NOTIFICATION_OPTIONS,
    _display_name,
    self_test as users_self_test,
)


class TelegramPanelRuntimeV34(TelegramPanelRuntimeV33):
    """Compatibility entrypoint for owner-managed user notification settings."""


def self_test() -> None:
    users_self_test()
    assert callable(_clone)
    assert callable(_merge_set_list)
    assert callable(_merge_value)
    assert len(ALL_SUMMARY_NOTIFICATION_OPTIONS) == 2
    assert _display_name({"first_name": "User"}, "2") == "User"
    print("admin_panel_runtime_v34 compatibility self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV34().run()


if __name__ == "__main__":
    raise SystemExit(main())
