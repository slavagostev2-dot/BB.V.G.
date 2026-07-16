from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import bot_notification_state
import bot_private_state
import source_tier_maintenance as legacy

legacy.notification_recipients = bot_notification_state.admin_recipients


def self_test() -> None:
    original = bot_private_state.STATE_PATH
    try:
        with TemporaryDirectory() as temporary:
            # A self-test must not decrypt the production bundle with a CI key.
            bot_private_state.STATE_PATH = Path(temporary) / "missing-state.enc.json"
            recipients = legacy.notification_recipients()
            assert isinstance(recipients, list)
    finally:
        bot_private_state.STATE_PATH = original
    print("BB V.G. source tier bot-only notification self-test passed")


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
