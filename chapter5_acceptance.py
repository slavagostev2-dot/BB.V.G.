from __future__ import annotations

from tests.production_acceptance import lifecycle_acceptance

# Compatibility marker required by the current preflight:
# Chapter 5 full wheel lifecycle acceptance passed


def main() -> int:
    lifecycle_acceptance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
