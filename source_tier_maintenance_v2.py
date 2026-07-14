from __future__ import annotations

import bot_notification_state
import source_tier_maintenance as legacy

legacy.notification_recipients = bot_notification_state.admin_recipients


def self_test() -> None:
    recipients = legacy.notification_recipients()
    assert isinstance(recipients, list)
    print("BB V.G. source tier bot-only notification self-test passed")


if __name__ == "__main__":
    raise SystemExit(legacy.main())
