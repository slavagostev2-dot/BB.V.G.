from __future__ import annotations

# Compatibility tombstone. The nightly source tier and its scheduler were
# removed; every approved source now lives in public_sources.txt and is checked
# by the primary monitor.
import monitor  # noqa: F401


def main() -> int:
    print("Nightly source monitoring has been removed; unified source base is active.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
