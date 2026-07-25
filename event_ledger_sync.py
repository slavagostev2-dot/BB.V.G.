from __future__ import annotations

import json

from bbvg.storage import EventStore
from bbvg.storage.github_sync import sync_from_environment


def main() -> int:
    result = sync_from_environment(EventStore())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if int(result.get("retry", 0) or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
