from __future__ import annotations

from tests.production_acceptance import ci_acceptance


def self_test() -> None:
    ci_acceptance()


if __name__ == "__main__":
    self_test()
