from __future__ import annotations

import argparse

from admin_panel_runtime_v17 import TelegramPanelRuntimeV17
from bbvg.bot.foundation import BRAND_NAME, MINIAPP_URL

MINIAPP_V4_URL = MINIAPP_URL


class TelegramPanelRuntimeV19(TelegramPanelRuntimeV17):
    """Compatibility entrypoint after consolidating Mini App behavior."""


def self_test() -> None:
    assert BRAND_NAME == "BB V.G."
    assert MINIAPP_V4_URL.endswith(".pages.dev/")
    print("admin_panel_runtime_v19 compatibility self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV19().run()


if __name__ == "__main__":
    raise SystemExit(main())
