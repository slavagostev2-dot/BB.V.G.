from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

import monitor_data as data_store

PRIMARY_DOMAIN = os.getenv("TELEGRAM_WEB_DOMAIN", "telegram.me").strip().casefold() or "telegram.me"
LEGACY_DOMAINS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
OUTAGE_PREFIX = "GLOBAL_TRANSPORT_OUTAGE:"
# A source request already has a direct HTTP path plus the resolved-IP curl fallback.
# Repeating every one of 167 sources several times inside the same cycle can turn a
# short systemic outage into a 20+ minute monitor stall. Keep only one additional
# whole-batch retry; the next regular monitor cycle is the next recovery attempt.
BATCH_ATTEMPTS = max(1, min(2, int(os.getenv("TRANSPORT_BATCH_ATTEMPTS", "2"))))
BACKOFF_SECONDS = (5, 15, 30, 60)
DNS_CACHE_SECONDS = max(60, int(os.getenv("TELEGRAM_DNS_CACHE_SECONDS", "1800")))
DNS_FAILURE_CACHE_SECONDS = max(
    30, min(300, int(os.getenv("TELEGRAM_DNS_FAILURE_CACHE_SECONDS", "60")))
)
SOURCE_REQUEST_TIMEOUT = max(
    5, min(10, int(os.getenv("TELEGRAM_SOURCE_TIMEOUT_SECONDS", "8")))
)
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
_domain_ipv4 = ""
_domain_ipv4_at = 0.0
_domain_resolve_error = ""
_domain_resolve_error_at = 0.0
_dns_lock = threading.Lock()


def is_transient_transport_error(value: object) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def clean_username(value: object) -> str:
    return str(value or "").strip().lstrip("@").strip("/")


def public_source_url(source: str, before: int | None = None) -> str:
    url = f"https://{PRIMARY_DOMAIN}/s/{clean_username(source)}"
    return f"{url}?before={int(before)}" if before is not None else url


def public_message_url(source: str, message_id: int) -> str:
    return f"https://{PRIMARY_DOMAIN}/{clean_username(source)}/{int(message_id)}"


def profile_url(source: str) -> str:
    return f"https://{PRIMARY_DOMAIN}/{clean_username(source)}"


def rewrite_telegram_url(value: str) -> str:
    raw = str(value or "")
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    hostname = (parts.hostname or "").casefold()
    if hostname not in LEGACY_DOMAINS:
        return raw
    netloc = PRIMARY_DOMAIN
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme or "https", netloc, parts.path, parts.query, parts.fragment))


def rewrite_telegram_text(value: str) -> str:
    return (
        str(value or "")
        .replace("https://www.t.me/", f"https://{PRIMARY_DOMAIN}/")
        .replace("http://www.t.me/", f"https://{PRIMARY_DOMAIN}/")
        .replace("https://t.me/", f"https://{PRIMARY_DOMAIN}/")
        .replace("http://t.me/", f"https://{PRIMARY_DOMAIN}/")
    )


def rewrite_markup(reply_markup: dict | None) -> dict | None:
    if not isinstance(reply_markup, dict):
        return reply_markup
    result = copy.deepcopy(reply_markup)
    for row in result.get("inline_keyboard", []):
        if not isinstance(row, list):
            continue
        for button in row:
            if isinstance(button, dict) and button.get("url"):
                button["url"] = rewrite_telegram_url(str(button["url"]))
    return result


def rewrite_nested_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: rewrite_nested_urls(child) for key, child in value.items()}
    if isinstance(value, list):
        return [rewrite_nested_urls(child) for child in value]
    if isinstance(value, str) and ("t.me/" in value or "www.t.me/" in value):
        return rewrite_telegram_text(value)
    return value


def _ipv4_answers(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    result: list[str] = []
    for answer in payload.get("Answer", []):
        if not isinstance(answer, dict) or int(answer.get("type", 0) or 0) != 1:
            continue
        value = str(answer.get("data") or "").strip()
        parts = value.split(".")
        if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
            result.append(value)
    return result


def resolve_primary_ipv4(timeout: int = 15) -> str:
    global _domain_ipv4, _domain_ipv4_at, _domain_resolve_error, _domain_resolve_error_at
    current = time.monotonic()
    if _domain_ipv4 and current - _domain_ipv4_at < DNS_CACHE_SECONDS:
        return _domain_ipv4
    if (
        _domain_resolve_error
        and current - _domain_resolve_error_at < DNS_FAILURE_CACHE_SECONDS
    ):
        raise requests.ConnectionError(_domain_resolve_error)
    with _dns_lock:
        current = time.monotonic()
        if _domain_ipv4 and current - _domain_ipv4_at < DNS_CACHE_SECONDS:
            return _domain_ipv4
        if (
            _domain_resolve_error
            and current - _domain_resolve_error_at < DNS_FAILURE_CACHE_SECONDS
        ):
            raise requests.ConnectionError(_domain_resolve_error)
        failures: list[str] = []
        for endpoint in DOH_ENDPOINTS:
            try:
                response = requests.get(
                    endpoint,
                    params={"name": PRIMARY_DOMAIN, "type": "A"},
                    headers={"Accept": "application/dns-json"},
                    timeout=max(5, min(timeout, 15)),
                )
                response.raise_for_status()
                addresses = _ipv4_answers(response.json())
                if addresses:
                    _domain_ipv4 = addresses[0]
                    _domain_ipv4_at = time.monotonic()
                    _domain_resolve_error = ""
                    _domain_resolve_error_at = 0.0
                    return _domain_ipv4
            except (requests.RequestException, ValueError, TypeError) as exc:
                failures.append(f"{urlsplit(endpoint).hostname}:{type(exc).__name__}")
        _domain_resolve_error = (
            f"DNS-over-HTTPS for {PRIMARY_DOMAIN} failed: " + ", ".join(failures)
        )
        _domain_resolve_error_at = time.monotonic()
        raise requests.ConnectionError(_domain_resolve_error)


def _curl_with_resolved_domain(
    method: str,
    url: str,
    *,
    timeout: int,
    headers: dict[str, str] | None,
) -> requests.Response:
    target = rewrite_telegram_url(url)
    address = resolve_primary_ipv4(timeout)
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
        f"{PRIMARY_DOMAIN}:443:{address}",
        "--write-out",
        "\n%{http_code}",
    ]
    for key, value in (headers or {}).items():
        command.extend(["--header", f"{key}: {value}"])
    command.append(target)
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
            f"curl {PRIMARY_DOMAIN} fallback failed ({completed.returncode}): "
            f"{completed.stderr.strip()[:500]}"
        )
    body, separator, status_text = completed.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        raise requests.ConnectionError("Telegram fallback returned no HTTP status")
    response = requests.Response()
    response.status_code = int(status_text)
    response.url = target
    response.encoding = "utf-8"
    response._content = body.encode("utf-8")
    response.request = requests.Request(method.upper(), target, headers=headers).prepare()
    return response


def _request_without_legacy_redirects(
    original_request: Callable,
    method: str,
    url: str,
    *,
    attempts: int,
    timeout: int,
    kwargs: dict[str, Any],
) -> requests.Response:
    current = rewrite_telegram_url(url)
    request_kwargs = dict(kwargs)
    request_kwargs["allow_redirects"] = False
    for _ in range(5):
        try:
            response = original_request(method, current, attempts=attempts, **request_kwargs)
        except requests.RequestException as direct_error:
            if not is_transient_transport_error(direct_error):
                raise
            response = _curl_with_resolved_domain(
                method,
                current,
                timeout=timeout,
                headers=request_kwargs.get("headers"),
            )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = str(response.headers.get("Location") or "").strip()
        if not location:
            return response
        current = rewrite_telegram_url(urljoin(current, location))
    raise requests.TooManyRedirects(f"Too many Telegram redirects for {url}")


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


def _mark_transport_attempt(data: dict[str, Any], source: str, at: datetime | None = None) -> None:
    at = at or data_store.now_utc()
    entry = data.setdefault("sources", {}).setdefault(source, {})
    entry["last_transport_outage_at"] = at.isoformat()
    entry["transport_outages"] = int(entry.get("transport_outages", 0) or 0) + 1
    entry["last_updated_at"] = at.isoformat()


def install(monitor_module: Any) -> None:
    global _outage_sources, _outage_detail
    if getattr(monitor_module, "_bbvg_telegram_transport_installed", False):
        return

    original_request: Callable = monitor_module.request_with_retries
    original_fetch_all: Callable = monitor_module.fetch_all_sources
    original_fetch_public: Callable = monitor_module.fetch_public_channel
    original_load_state: Callable = monitor_module.load_state
    original_send_message: Callable = monitor_module.send_message
    original_record_problem: Callable = data_store.record_source_problem
    original_record_stats: Callable = data_store.record_source_check_stats

    def primary_request(method: str, url: str, *, attempts: int = 3, **kwargs):
        hostname = (urlsplit(str(url)).hostname or "").casefold()
        if hostname not in LEGACY_DOMAINS:
            return original_request(method, url, attempts=attempts, **kwargs)
        request_kwargs = dict(kwargs)
        timeout_value = request_kwargs.get("timeout", monitor_module.REQUEST_TIMEOUT)
        try:
            timeout = max(5, min(int(timeout_value), SOURCE_REQUEST_TIMEOUT))
        except (TypeError, ValueError):
            timeout = SOURCE_REQUEST_TIMEOUT
        request_kwargs["timeout"] = timeout
        # Batch-level recovery already retries a systemic outage. A single source
        # therefore gets one direct attempt plus the independent resolved-IP
        # fallback instead of three serial 15-second attempts.
        return _request_without_legacy_redirects(
            original_request,
            method,
            url,
            attempts=1,
            timeout=timeout,
            kwargs=request_kwargs,
        )

    def primary_fetch_public(username: str):
        messages = original_fetch_public(username)
        return [
            monitor_module.Message(
                source=message.source,
                message_id=message.message_id,
                date=message.date,
                text=rewrite_telegram_text(message.text),
                message_url=public_message_url(message.source or username, message.message_id),
            )
            for message in messages
        ]

    def primary_load_state():
        return rewrite_nested_urls(original_load_state())

    def primary_send_message(text: str, url: str | None = None, reply_markup: dict | None = None):
        return original_send_message(
            rewrite_telegram_text(text),
            url=rewrite_telegram_url(url) if url else None,
            reply_markup=rewrite_markup(reply_markup),
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
            if attempt < BATCH_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        results, errors, empty = last_result
        _outage_sources = {source.casefold() for source in sources}
        _outage_detail = next(iter(errors.values()), "temporary Telegram transport failure")
        return results, {
            source: f"{OUTAGE_PREFIX} {detail}" for source, detail in errors.items()
        }, empty

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

    monitor_module.request_with_retries = primary_request
    monitor_module.fetch_public_channel = primary_fetch_public
    monitor_module.fetch_all_sources = resilient_fetch_all
    monitor_module.load_state = primary_load_state
    monitor_module.send_message = primary_send_message
    data_store.record_source_problem = resilient_record_problem
    data_store.record_source_check_stats = resilient_record_stats
    monitor_module.TELEGRAM_WEB_DOMAIN = PRIMARY_DOMAIN
    monitor_module.telegram_public_url = public_source_url
    monitor_module.telegram_message_url = public_message_url
    monitor_module._bbvg_telegram_transport_installed = True


def self_test() -> None:
    assert PRIMARY_DOMAIN == "telegram.me" or PRIMARY_DOMAIN
    assert public_source_url("@test") == f"https://{PRIMARY_DOMAIN}/s/test"
    assert public_source_url("test", 100).endswith("/s/test?before=100")
    assert public_message_url("test", 7) == f"https://{PRIMARY_DOMAIN}/test/7"
    assert profile_url("@test") == f"https://{PRIMARY_DOMAIN}/test"
    assert rewrite_telegram_url("https://t.me/s/test") == f"https://{PRIMARY_DOMAIN}/s/test"
    assert "t.me/" not in rewrite_telegram_text("https://t.me/test/1").replace("telegram.me/", "")
    assert is_transient_transport_error("NameResolutionError: failed to resolve")
    assert _ipv4_answers({"Answer": [{"type": 1, "data": "149.154.167.99"}]}) == ["149.154.167.99"]
    assert 1 <= BATCH_ATTEMPTS <= 2
    assert 5 <= SOURCE_REQUEST_TIMEOUT <= 10
    print("telegram_transport self-test passed")


if __name__ == "__main__":
    self_test()
