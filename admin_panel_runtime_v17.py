from __future__ import annotations

import argparse

from bbvg.bot.source_requests import (
    SOURCE_REQUESTS_PATH,
    SOURCE_REQUEST_PREFIX,
    SourceRequestRuntime,
    default_source_requests,
    self_test as source_requests_self_test,
)


class TelegramPanelRuntimeV17(SourceRequestRuntime):
    """Compatibility entrypoint for the consolidated source request subsystem."""


def self_test() -> None:
    source_requests_self_test()
    print("admin_panel_runtime_v17 compatibility self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV17().run()


if __name__ == "__main__":
    raise SystemExit(main())
