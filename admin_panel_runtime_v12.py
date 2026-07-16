from __future__ import annotations

import argparse

from admin_panel_runtime_v11 import TelegramPanelRuntimeV11
from bbvg.bot.foundation import self_test as foundation_self_test


class TelegramPanelRuntimeV12(TelegramPanelRuntimeV11):
    """Compatibility entrypoint for the consolidated panel foundation."""


def self_test() -> None:
    foundation_self_test()
    print("admin_panel_runtime_v12 compatibility self-test passed")


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
