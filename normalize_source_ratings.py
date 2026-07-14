from __future__ import annotations

import json
from pathlib import Path

import rating_policy

ROOT = Path(__file__).resolve().parent
STATS_PATH = ROOT / "source_stats.json"


def main() -> int:
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "sources": {}, "daily": {}}
    if not isinstance(data, dict):
        data = {"version": 1, "sources": {}, "daily": {}}
    changed = rating_policy.normalize_additive_rating(data)
    if changed:
        temporary = STATS_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(STATS_PATH)
    print(f"Additive source rating normalization: {'changed' if changed else 'unchanged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
