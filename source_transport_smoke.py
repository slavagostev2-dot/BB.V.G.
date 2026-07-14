from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import bbvg_monitor_main as runtime
import monitor_data as data_store
import telegram_transport

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "source_transport_state.json"
EXPECTED = 66
UTC = timezone.utc


def main() -> int:
    monitor = runtime.monitor
    configured = monitor.read_list(ROOT / "public_sources.txt")
    sources = data_store.operational_sources(configured, "fast")
    started = time.monotonic()
    checked_at = datetime.now(UTC).isoformat()

    if len(configured) != EXPECTED or len(sources) != EXPECTED:
        payload = {
            "status": "failure",
            "checked_at": checked_at,
            "domain": telegram_transport.PRIMARY_DOMAIN,
            "configured_sources": len(configured),
            "operational_sources": len(sources),
            "expected_sources": EXPECTED,
            "error": "source inventory mismatch",
        }
        OUTPUT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    messages_by_source, errors, empty = monitor.fetch_all_sources(sources)
    accounted = set(messages_by_source) | set(errors) | set(empty)
    missing = [source for source in sources if source not in accounted]
    duration = round(time.monotonic() - started, 3)
    transport_errors = {
        source: detail[:700]
        for source, detail in errors.items()
    }
    payload = {
        "version": 1,
        "status": "success" if len(accounted) == EXPECTED and not missing else "failure",
        "checked_at": checked_at,
        "domain": telegram_transport.PRIMARY_DOMAIN,
        "expected_sources": EXPECTED,
        "configured_sources": len(configured),
        "operational_sources": len(sources),
        "accounted_sources": len(accounted),
        "reachable_sources": len(messages_by_source),
        "empty_sources": len(empty),
        "error_sources": len(errors),
        "missing_sources": missing,
        "duration_seconds": duration,
        "message_count": sum(len(messages) for messages in messages_by_source.values()),
        "errors": transport_errors,
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
