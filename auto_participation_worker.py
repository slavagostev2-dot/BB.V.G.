from __future__ import annotations

import json

import bbvg_monitor_main as runtime
import betboom_auto_participation


def main() -> int:
    monitor = runtime.monitor
    state = monitor.load_state()
    result = betboom_auto_participation.process_active_wheels(state, monitor)
    if bool(result.get("changed")):
        monitor.save_state(state)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
