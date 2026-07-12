"""Backward-compatible entry point for the merged Telegram-only monitor."""

from monitor import main


if __name__ == "__main__":
    raise SystemExit(main())
