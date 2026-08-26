from __future__ import annotations

import source_intelligence_alerts
import source_intelligence_entry as entry


def main() -> int:
    module = entry.source_intelligence
    state = module.load_state()
    sent = source_intelligence_alerts.notify_new_candidates(module, state)
    if sent:
        module.save_state(state)
    print(f"Unresolved source reminders sent: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
