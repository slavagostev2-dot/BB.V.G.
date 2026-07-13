from __future__ import annotations

import monitor
import nightly_discovery
import notification_router

notification_router.install(monitor)

if __name__ == "__main__":
    raise SystemExit(nightly_discovery.main())
