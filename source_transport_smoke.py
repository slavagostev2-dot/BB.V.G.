from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from monitor import read_list as read_static_source_list
import bbvg_monitor_main as runtime
import monitor_data as data_store
import telegram_transport

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "source_transport_state.json"
EXPECTED = 66
UTC = timezone.utc


def transport_status(
    source_count: int,
    accounted_count: int,
    missing: list[str],
    errors: dict[str, str],
) -> str:
    complete = accounted_count == source_count and not missing
    if not complete:
        return "failure"
    return "success" if not errors else "degraded"


def main() -> int:
    monitor = runtime.monitor
    # bbvg_monitor_main intentionally extends public_sources.txt with private
    # aliases from the encrypted workflow configuration. Keep the checked union
    # intact, but record the public inventory separately so System Health can
    # compare like with like without knowing private configuration.
    public = read_static_source_list(ROOT / "public_sources.txt")
    primary_with_private = monitor.read_list(ROOT / "public_sources.txt")
    nightly = read_static_source_list(ROOT / "source_catalog.txt")
    sources = data_store.operational_sources(primary_with_private, "fast")
    sources += data_store.operational_sources(nightly, "nightly")
    configured = primary_with_private + nightly
    private_count = max(0, len(primary_with_private) - len(public))
    started = time.monotonic()
    checked_at = datetime.now(UTC).isoformat()

    if len(configured) < EXPECTED or len(sources) < EXPECTED or len({value.casefold() for value in sources}) != len(sources):
        payload = {
            "status": "failure",
            "checked_at": checked_at,
            "domain": telegram_transport.PRIMARY_DOMAIN,
            "configured_sources": len(configured),
            "operational_sources": len(sources),
            "expected_sources": EXPECTED,
            "error": "source inventory mismatch",
        }
        data_store.atomic_write_json(OUTPUT_PATH, payload)
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
    status = transport_status(len(sources), len(accounted), missing, transport_errors)
    payload = {
        "version": 1,
        "status": status,
        "checked_at": checked_at,
        "domain": telegram_transport.PRIMARY_DOMAIN,
        "expected_sources": EXPECTED,
        "configured_sources": len(configured),
        "operational_sources": len(sources),
        "primary_sources": len(public),
        "private_sources": private_count,
        "nightly_sources": len(nightly),
        "accounted_sources": len(accounted),
        "reachable_sources": len(messages_by_source),
        "empty_sources": len(empty),
        "error_sources": len(errors),
        "missing_sources": missing,
        "duration_seconds": duration,
        "message_count": sum(len(messages) for messages in messages_by_source.values()),
        "errors": transport_errors,
    }
    data_store.atomic_write_json(OUTPUT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
