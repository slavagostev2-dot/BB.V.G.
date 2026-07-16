from __future__ import annotations

from tests.production_acceptance import unified_logic_acceptance


def self_test() -> None:
    unified_logic_acceptance()


if __name__ == "__main__":
    self_test()
