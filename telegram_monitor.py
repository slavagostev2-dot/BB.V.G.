"""Backward-compatible entry point for the merged Telegram-only monitor."""

from monitor import main

# Service restart marker: 2026-07-16 Gorilla wheel recovery.

if __name__ == "__main__":
    raise SystemExit(main())
