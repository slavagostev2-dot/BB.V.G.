from __future__ import annotations

import argparse
import json
import os
from typing import Any

import admin_action as legacy
import rating_policy

_original_recorder = legacy.monitor.data_store.record_admin_wheel_decision


def _additive_recorder(
    data: dict[str, Any],
    *,
    wheel_key: str,
    sources: list[str],
    decision: str,
    actor: str = "admin",
    at: Any = None,
) -> bool:
    return rating_policy.record_admin_wheel_decision(
        data,
        wheel_key=wheel_key,
        sources=sources,
        decision=decision,
        actor=actor,
        at=at,
        recorder=_original_recorder,
    )


legacy.monitor.data_store.record_admin_wheel_decision = _additive_recorder


def self_test() -> None:
    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
    changed = _additive_recorder(
        stats,
        wheel_key="wheel-a",
        sources=["sourceA"],
        decision="confirmed",
    )
    assert changed is True
    assert stats["sources"]["sourceA"]["quality_score"] == 40

    changed = _additive_recorder(
        stats,
        wheel_key="wheel-a",
        sources=["sourceA"],
        decision="inactive",
    )
    assert changed is False
    assert stats["sources"]["sourceA"]["quality_score"] == 40

    changed = _additive_recorder(
        stats,
        wheel_key="wheel-b",
        sources=["sourceB"],
        decision="inactive",
    )
    assert changed is True
    assert stats["sources"]["sourceB"]["quality_score"] == 0
    assert all(
        int(entry.get("quality_score", 0) or 0) >= 0
        for entry in stats["sources"].values()
    )
    print("BB V.G. additive administrator action self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--action", default=os.getenv("ADMIN_ACTION", ""))
    parser.add_argument("--value", default=os.getenv("ADMIN_VALUE", ""))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.action or not args.value:
        raise SystemExit("ADMIN_ACTION and ADMIN_VALUE are required")
    result = legacy.run_action(args.action, args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
