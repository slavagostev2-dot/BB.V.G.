from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Callable

import monitor_data as data_store


OUTAGE_PREFIX = "GLOBAL_TRANSPORT_OUTAGE:"
BATCH_ATTEMPTS = max(2, min(5, int(os.getenv("TRANSPORT_BATCH_ATTEMPTS", "3"))))
BACKOFF_SECONDS = (5, 15, 30, 60)

_TRANSIENT_MARKERS = (
    "nameresolutionerror",
    "failed to resolve",
    "temporary failure in name resolution",
    "name or service not known",
    "connectionerror",
    "connecttimeout",
    "readtimeout",
    "proxyerror",
    "remotedisconnected",
    "connection reset",
    "network is unreachable",
)

_outage_sources: set[str] = set()
_outage_detail = ""


def is_transient_transport_error(value: object) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def is_systemic_transport_outage(
    sources: list[str],
    results: dict[str, Any],
    errors: dict[str, str],
    empty: list[str],
) -> bool:
    if not sources or results or empty or len(errors) < len(sources):
        return False
    return all(is_transient_transport_error(errors.get(source, "")) for source in sources)


def outage_active() -> bool:
    return bool(_outage_sources)


def outage_detail() -> str:
    return _outage_detail


def _mark_transport_attempt(
    data: dict[str, Any], source: str, at: datetime | None = None
) -> None:
    at = at or data_store.now_utc()
    entry = data.setdefault("sources", {}).setdefault(source, {})
    entry["last_transport_outage_at"] = at.isoformat()
    entry["transport_outages"] = int(entry.get("transport_outages", 0) or 0) + 1
    entry["last_updated_at"] = at.isoformat()


def install(monitor_module: Any) -> None:
    """Install retry and accounting guards around the existing monitor runtime."""
    global _outage_sources, _outage_detail

    if getattr(monitor_module, "_bbvg_resilience_installed", False):
        return

    original_fetch_all: Callable = monitor_module.fetch_all_sources
    original_record_problem: Callable = data_store.record_source_problem
    original_record_stats: Callable = data_store.record_source_check_stats

    def resilient_fetch_all(sources: list[str]):
        global _outage_sources, _outage_detail
        _outage_sources = set()
        _outage_detail = ""

        last_result = ({}, {}, [])
        for attempt in range(1, BATCH_ATTEMPTS + 1):
            last_result = original_fetch_all(sources)
            results, errors, empty = last_result
            if not is_systemic_transport_outage(sources, results, errors, empty):
                return last_result

            sample = next(iter(errors.values()), "temporary Telegram transport failure")
            print(
                "WARNING systemic Telegram transport outage detected; "
                f"batch attempt {attempt}/{BATCH_ATTEMPTS}: {sample[:300]}"
            )
            if attempt < BATCH_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])

        results, errors, empty = last_result
        _outage_sources = {source.casefold() for source in sources}
        _outage_detail = next(iter(errors.values()), "temporary Telegram transport failure")
        tagged = {
            source: f"{OUTAGE_PREFIX} {detail}"
            for source, detail in errors.items()
        }
        return results, tagged, empty

    def resilient_record_problem(
        data: dict[str, Any],
        username: str,
        kind: str,
        error: str = "",
        at: datetime | None = None,
    ) -> bool:
        if kind == "error" and str(error).startswith(OUTAGE_PREFIX):
            at = at or data_store.now_utc()
            entry = data.setdefault("sources", {}).setdefault(username, {})
            entry.setdefault("checks", 0)
            entry.setdefault("successful_checks", 0)
            entry.setdefault("consecutive_errors", 0)
            entry.setdefault("consecutive_empty", 0)
            entry.setdefault("status", "unknown")
            entry["last_transport_outage_at"] = at.isoformat()
            entry["last_transport_error"] = str(error)[len(OUTAGE_PREFIX):].strip()[:500]
            entry["transport_outages"] = int(entry.get("transport_outages", 0) or 0) + 1
            # Preserve the source's previous operational status and failure counters.
            # A runner-wide DNS outage is not evidence that the channel is broken.
            return False
        return original_record_problem(data, username, kind, error, at)

    def resilient_record_stats(
        data: dict[str, Any],
        source: str,
        status: str,
        messages_count: int = 0,
        at: datetime | None = None,
    ) -> None:
        if status == "error" and source.casefold() in _outage_sources:
            _mark_transport_attempt(data, source, at)
            return
        original_record_stats(data, source, status, messages_count, at)

    monitor_module.fetch_all_sources = resilient_fetch_all
    data_store.record_source_problem = resilient_record_problem
    data_store.record_source_check_stats = resilient_record_stats
    monitor_module._bbvg_resilience_installed = True


def self_test() -> None:
    sources = ["one", "two"]
    errors = {
        "one": "ConnectionError: Failed to resolve 't.me'",
        "two": "NameResolutionError: name or service not known",
    }
    assert is_transient_transport_error(errors["one"])
    assert is_systemic_transport_outage(sources, {}, errors, [])
    assert not is_systemic_transport_outage(sources, {"one": []}, errors, [])
    assert not is_systemic_transport_outage(sources, {}, errors, ["two"])
    assert not is_transient_transport_error("HTTP 404")
    print("monitor_resilience self-test passed")


if __name__ == "__main__":
    self_test()
