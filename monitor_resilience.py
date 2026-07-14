from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests

import monitor_data as data_store


OUTAGE_PREFIX = "GLOBAL_TRANSPORT_OUTAGE:"
BATCH_ATTEMPTS = max(2, min(5, int(os.getenv("TRANSPORT_BATCH_ATTEMPTS", "3"))))
BACKOFF_SECONDS = (5, 15, 30, 60)
DNS_CACHE_SECONDS = max(60, int(os.getenv("TELEGRAM_DNS_CACHE_SECONDS", "1800")))
WEB_DOMAINS = ("t.me", "telegram.me")
DOH_ENDPOINTS = (
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
)

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
_telegram_ipv4 = ""
_telegram_ipv4_at = 0.0
_dns_lock = threading.Lock()


def is_transient_transport_error(value: object) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def build_source_urls(source: str) -> list[str]:
    clean = str(source).strip().lstrip("@").strip("/")
    return [f"https://{domain}/s/{clean}" for domain in WEB_DOMAINS]


def alternate_telegram_url(url: str) -> str:
    parts = urlsplit(str(url))
    if (parts.hostname or "").casefold() != "t.me":
        return str(url)
    netloc = "telegram.me"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme or "https", netloc, parts.path, parts.query, parts.fragment))


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


def _ipv4_answers(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    result: list[str] = []
    for answer in payload.get("Answer", []):
        if not isinstance(answer, dict) or int(answer.get("type", 0) or 0) != 1:
            continue
        value = str(answer.get("data") or "").strip()
        parts = value.split(".")
        if len(parts) == 4 and all(
            part.isdigit() and 0 <= int(part) <= 255 for part in parts
        ):
            result.append(value)
    return result


def _resolve_telegram_ipv4(timeout: int) -> str:
    global _telegram_ipv4, _telegram_ipv4_at
    current = time.monotonic()
    if _telegram_ipv4 and current - _telegram_ipv4_at < DNS_CACHE_SECONDS:
        return _telegram_ipv4

    with _dns_lock:
        current = time.monotonic()
        if _telegram_ipv4 and current - _telegram_ipv4_at < DNS_CACHE_SECONDS:
            return _telegram_ipv4

        failures: list[str] = []
        for endpoint in DOH_ENDPOINTS:
            try:
                response = requests.get(
                    endpoint,
                    params={"name": "t.me", "type": "A"},
                    headers={"Accept": "application/dns-json"},
                    timeout=max(5, min(timeout, 15)),
                )
                response.raise_for_status()
                addresses = _ipv4_answers(response.json())
                if addresses:
                    _telegram_ipv4 = addresses[0]
                    _telegram_ipv4_at = time.monotonic()
                    print(f"Telegram DNS fallback resolved t.me to {_telegram_ipv4}")
                    return _telegram_ipv4
            except (requests.RequestException, ValueError, TypeError) as exc:
                failures.append(f"{urlsplit(endpoint).hostname}:{type(exc).__name__}")
        raise requests.ConnectionError(
            "Telegram DNS-over-HTTPS fallback failed: " + ", ".join(failures)
        )


def _curl_with_resolved_telegram(
    method: str,
    url: str,
    *,
    timeout: int,
    headers: dict[str, str] | None,
    allow_redirects: bool,
) -> requests.Response:
    address = _resolve_telegram_ipv4(timeout)
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--compressed",
        "--request",
        method.upper(),
        "--connect-timeout",
        str(max(3, min(timeout, 10))),
        "--max-time",
        str(max(5, timeout)),
        "--resolve",
        f"t.me:443:{address}",
        "--write-out",
        "\n%{http_code}",
    ]
    if allow_redirects:
        command.append("--location")
    for key, value in (headers or {}).items():
        command.extend(["--header", f"{key}: {value}"])
    command.append(url)

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(10, timeout + 10),
        check=False,
    )
    if completed.returncode != 0:
        raise requests.ConnectionError(
            f"curl Telegram fallback failed ({completed.returncode}): "
            f"{completed.stderr.strip()[:500]}"
        )
    body, separator, status_text = completed.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        raise requests.ConnectionError("curl Telegram fallback returned no HTTP status")

    response = requests.Response()
    response.status_code = int(status_text)
    response.url = url
    response.encoding = "utf-8"
    response._content = body.encode("utf-8")
    response.request = requests.Request(method.upper(), url, headers=headers).prepare()
    return response


def _mark_transport_attempt(
    data: dict[str, Any], source: str, at: datetime | None = None
) -> None:
    at = at or data_store.now_utc()
    entry = data.setdefault("sources", {}).setdefault(source, {})
    entry["last_transport_outage_at"] = at.isoformat()
    entry["transport_outages"] = int(entry.get("transport_outages", 0) or 0) + 1
    entry["last_updated_at"] = at.isoformat()


def install(monitor_module: Any) -> None:
    """Install retry, DNS fallback and accounting guards around the monitor."""
    global _outage_sources, _outage_detail

    if getattr(monitor_module, "_bbvg_resilience_installed", False):
        return

    original_request: Callable = monitor_module.request_with_retries
    original_fetch_all: Callable = monitor_module.fetch_all_sources
    original_record_problem: Callable = data_store.record_source_problem
    original_record_stats: Callable = data_store.record_source_check_stats

    def resilient_request(method: str, url: str, *, attempts: int = 3, **kwargs):
        hostname = (urlsplit(str(url)).hostname or "").casefold()
        timeout_value = kwargs.get("timeout", monitor_module.REQUEST_TIMEOUT)
        try:
            timeout = max(5, int(timeout_value))
        except (TypeError, ValueError):
            timeout = int(monitor_module.REQUEST_TIMEOUT)

        try:
            return original_request(method, url, attempts=attempts, **kwargs)
        except requests.RequestException as direct_error:
            if hostname != "t.me" or not is_transient_transport_error(direct_error):
                raise

            alternate = alternate_telegram_url(url)
            try:
                print("WARNING t.me failed; trying telegram.me endpoint")
                return original_request(method, alternate, attempts=attempts, **kwargs)
            except requests.RequestException as alternate_error:
                if not is_transient_transport_error(alternate_error):
                    raise
                print(
                    "WARNING Telegram web domains failed; using DNS-over-HTTPS "
                    "with TLS verification"
                )
                return _curl_with_resolved_telegram(
                    method,
                    url,
                    timeout=timeout,
                    headers=kwargs.get("headers"),
                    allow_redirects=bool(kwargs.get("allow_redirects", True)),
                )

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

    monitor_module.request_with_retries = resilient_request
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
    assert build_source_urls("test") == [
        "https://t.me/s/test",
        "https://telegram.me/s/test",
    ]
    assert alternate_telegram_url("https://t.me/s/test") == "https://telegram.me/s/test"
    assert is_transient_transport_error(errors["one"])
    assert is_systemic_transport_outage(sources, {}, errors, [])
    assert not is_systemic_transport_outage(sources, {"one": []}, errors, [])
    assert not is_systemic_transport_outage(sources, {}, errors, ["two"])
    assert not is_transient_transport_error("HTTP 404")
    assert _ipv4_answers({"Answer": [{"type": 1, "data": "149.154.167.99"}]}) == [
        "149.154.167.99"
    ]
    assert _ipv4_answers({"Answer": [{"type": 28, "data": "2001:db8::1"}]}) == []
    print("monitor_resilience self-test passed")


if __name__ == "__main__":
    self_test()
