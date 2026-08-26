from __future__ import annotations

import json
import os
from pathlib import Path

import betboom_auto_participation as auto
import betboom_participation_browser as browser


RESULT_PATH = Path("auto_participation_probe_result.json")
DEFAULT_PROBE_URL = "https://betboom.ru/freestream/zonertg11"


def _result_payload(url: str, result: auto.ParticipationResult) -> dict[str, object]:
    return {
        "success": bool(result.success),
        "status": str(result.status),
        "detail": str(result.detail)[:1000],
        "artifact_url": str(result.artifact_url or ""),
        "url": url,
    }


def main() -> int:
    url = os.getenv("BETBOOM_PROBE_URL", DEFAULT_PROBE_URL).strip()
    if not url.startswith("https://betboom.ru/freestream/"):
        payload = {
            "success": False,
            "status": "invalid_probe_url",
            "detail": "BETBOOM_PROBE_URL must point to betboom.ru/freestream/",
            "artifact_url": "",
            "url": url,
        }
        RESULT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    if auto._storage_state() is None:
        payload = {
            "success": False,
            "status": "not_configured",
            "detail": "Primary BetBoom browser session is not configured",
            "artifact_url": "",
            "url": url,
        }
        RESULT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    result = browser.participate(url)
    payload = _result_payload(url, result)
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
