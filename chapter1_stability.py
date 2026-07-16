from __future__ import annotations

from tests.production_acceptance import stability_acceptance


def self_test() -> None:
    stability_acceptance()


if __name__ == "__main__":
    self_test()
