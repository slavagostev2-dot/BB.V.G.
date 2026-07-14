from __future__ import annotations

import bot_notification_state
import daily_report
import monitor
import notification_navigation
import notification_router

notification_router.load_config = bot_notification_state.load_config
notification_router.install(monitor)
notification_navigation.install(monitor)

if __name__ == "__main__":
    raise SystemExit(daily_report.main())
