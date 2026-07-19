from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import bot_private_state
import source_tier_maintenance as legacy


def self_test() -> None:
    original = bot_private_state.STATE_PATH
    try:
        with TemporaryDirectory() as temporary:
            # A self-test must not decrypt the production bundle with a CI key.
            bot_private_state.STATE_PATH = Path(temporary) / "missing-state.enc.json"
            assert not hasattr(legacy, "send_notification")
            assert "manual_nightly_only" in legacy.main.__code__.co_consts
    finally:
        bot_private_state.STATE_PATH = original
    print("BB V.G. manual-only source tier self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
