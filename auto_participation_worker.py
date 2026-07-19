from __future__ import annotations

import json
import os

import bbvg_monitor_main as runtime
import betboom_auto_participation


def main() -> int:
    # The legacy five-minute schedule may still start this workflow, but scheduled
    # runs must never open BetBoom. Real participation is event-driven only.
    if os.getenv("GITHUB_EVENT_NAME", "").strip().casefold() == "schedule":
        print(json.dumps({"changed": False, "attempted": 0, "succeeded": 0, "failed": 0, "skipped": "scheduled_run"}, ensure_ascii=False, sort_keys=True))
        return 0

    monitor = runtime.monitor
    state = monitor.load_state()
    result = betboom_auto_participation.process_new_wheel_events(state, monitor)
    if bool(result.get("changed")):
        monitor.save_state(state)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
