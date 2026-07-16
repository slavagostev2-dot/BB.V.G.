from __future__ import annotations

import argparse

from bbvg.bot.foundation import MINIAPP_RELEASE, MINIAPP_URL
from bbvg.bot.sources import (
    SOURCE_REGISTRY_PATH,
    SourceRegistryRuntime,
    self_test as sources_self_test,
)

CONFIRMED_POINTS = 40
INACTIVE_POINTS = -45


class TelegramPanelRuntimeV22(SourceRegistryRuntime):
    """Compatibility entrypoint for the consolidated source registry subsystem."""


def self_test() -> None:
    sources_self_test()
    assert CONFIRMED_POINTS == 40
    assert INACTIVE_POINTS == -45
    assert MINIAPP_RELEASE == "5.11.0"
    assert MINIAPP_URL.startswith("https://")
    assert SOURCE_REGISTRY_PATH == "source_registry.json"
    assert hasattr(TelegramPanelRuntimeV22, "load_source_registry")
    assert hasattr(TelegramPanelRuntimeV22, "source_registry_fallback")
    print("admin_panel_runtime_v22 compatibility self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV22().run()


if __name__ == "__main__":
    raise SystemExit(main())
