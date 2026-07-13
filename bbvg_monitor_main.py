from __future__ import annotations

import bbvg_monitor_runtime as runtime


monitor = runtime.monitor
_original_recover_deadline = runtime.base_runtime._recover_deadline


def recover_deadline_manual_first(state: dict, key: str, entry: dict):
    normalized = str(key or "").casefold()
    manual = state.get("manual_deadlines", {}).get(normalized)
    if isinstance(manual, dict):
        deadline = monitor.parse_datetime(manual.get("deadline"))
        if deadline:
            return deadline
    if str(entry.get("deadline_source") or "") == "manual":
        deadline = monitor.parse_datetime(entry.get("deadline"))
        if deadline:
            return deadline
    return _original_recover_deadline(state, normalized, entry)


runtime.base_runtime._recover_deadline = recover_deadline_manual_first


if __name__ == "__main__":
    raise SystemExit(monitor.main())
