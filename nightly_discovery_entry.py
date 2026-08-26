from __future__ import annotations

import monitor
import telegram_transport

# Preserve the approved Telegram transport initialization for import safety.
telegram_transport.install(monitor)


def fetch_page_on_primary_domain(*_args, **_kwargs):
    raise RuntimeError("Nightly source monitoring has been removed")


def main() -> int:
    print("Nightly source monitoring has been removed; nothing to run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
