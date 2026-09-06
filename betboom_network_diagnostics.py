from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

UTC = timezone.utc
MAX_EVENTS = 120
MAX_BODY_CHARS = 1600
_ALLOWED_JSON_KEYS = {
    "success",
    "status",
    "statuscode",
    "code",
    "error",
    "errors",
    "message",
    "messages",
    "reason",
    "detail",
    "description",
    "result",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?:token|authorization|cookie|session|password|passwd|secret|phone|email|login|jwt|refresh|access[_-]?key)",
    re.IGNORECASE,
)
_TOKENISH_RE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+\-/=]+|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]{10,})?|[A-Za-z0-9_-]{96,})",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _betboom_url(value: Any) -> str:
    try:
        parsed = urlsplit(str(value or ""))
    except Exception:
        return ""
    host = str(parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (host == "betboom.ru" or host.endswith(".betboom.ru")):
        return ""
    # Query strings can contain campaign/session material and are not needed to
    # diagnose the participation endpoint. Keep only scheme, host/port and path.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    text = _TOKENISH_RE.sub("<redacted>", text)
    return text[:500]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return _safe_scalar(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:30]:
            key = str(raw_key)
            if _SENSITIVE_KEY_RE.search(key):
                result[key] = "<redacted>"
            else:
                result[key] = _safe_value(raw_value, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:20]]
    return _safe_scalar(value)


def _extract_safe_json(value: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def walk(node: Any, path: tuple[str, ...], depth: int) -> None:
        if depth > 4:
            return
        if isinstance(node, dict):
            for raw_key, raw_value in list(node.items())[:80]:
                key = str(raw_key)
                lowered = key.casefold().replace("_", "").replace("-", "")
                if _SENSITIVE_KEY_RE.search(key):
                    continue
                next_path = (*path, key)
                if lowered in _ALLOWED_JSON_KEYS:
                    found[".".join(next_path)] = _safe_value(raw_value)
                if isinstance(raw_value, (dict, list)):
                    walk(raw_value, next_path, depth + 1)
        elif isinstance(node, list):
            for index, item in enumerate(node[:20]):
                if isinstance(item, (dict, list)):
                    walk(item, (*path, str(index)), depth + 1)

    walk(value, (), 0)
    return found


def _safe_response_body(text: Any, content_type: Any) -> Any:
    if "json" not in str(content_type or "").casefold():
        return None
    raw = str(text or "")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    safe = _extract_safe_json(parsed)
    if not safe:
        return None
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= MAX_BODY_CHARS:
        return safe
    return encoded[:MAX_BODY_CHARS]


def _request_is_relevant(request: Any) -> bool:
    try:
        resource_type = str(request.resource_type or "").casefold()
        return resource_type in {"xhr", "fetch"} and bool(_betboom_url(request.url))
    except Exception:
        return False


def _append(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    events.append(event)
    if len(events) > MAX_EVENTS:
        del events[: len(events) - MAX_EVENTS]


def attach(page: Any) -> list[dict[str, Any]]:
    """Attach a bounded, secret-safe XHR/fetch trace to one Playwright page."""

    events: list[dict[str, Any]] = []

    def on_request(request: Any) -> None:
        if not _request_is_relevant(request):
            return
        _append(
            events,
            {
                "captured_at": _now(),
                "kind": "request",
                "method": str(request.method or ""),
                "resource_type": str(request.resource_type or ""),
                "url": _betboom_url(request.url),
            },
        )

    def on_response(response: Any) -> None:
        try:
            request = response.request
        except Exception:
            return
        if not _request_is_relevant(request):
            return
        try:
            headers = response.headers or {}
        except Exception:
            headers = {}
        content_type = str(headers.get("content-type") or "")
        event: dict[str, Any] = {
            "captured_at": _now(),
            "kind": "response",
            "method": str(request.method or ""),
            "resource_type": str(request.resource_type or ""),
            "url": _betboom_url(response.url),
            "status": int(response.status),
            "status_text": str(response.status_text or "")[:120],
            "content_type": content_type[:160],
        }
        if "json" in content_type.casefold():
            try:
                body = _safe_response_body(response.text(), content_type)
            except Exception:
                body = None
            if body is not None:
                event["body"] = body
        _append(events, event)

    def on_failed(request: Any) -> None:
        if not _request_is_relevant(request):
            return
        try:
            failure = request.failure
        except Exception:
            failure = ""
        _append(
            events,
            {
                "captured_at": _now(),
                "kind": "request_failed",
                "method": str(getattr(request, "method", "") or ""),
                "resource_type": str(getattr(request, "resource_type", "") or ""),
                "url": _betboom_url(getattr(request, "url", "")),
                "failure": _safe_scalar(failure),
            },
        )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    return events


def write_trace(target: Any, events: list[dict[str, Any]]) -> None:
    if not target:
        return
    path = Path(str(target))
    if not path.is_dir():
        return
    payload = {
        "captured_at": _now(),
        "policy": "betboom_xhr_fetch_only_no_headers_no_request_bodies_query_stripped_json_allowlist",
        "event_count": len(events),
        "events": list(events[-MAX_EVENTS:]),
    }
    try:
        (path / "network.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def self_test() -> None:
    assert _betboom_url("https://betboom.ru/api/test?token=secret#x") == "https://betboom.ru/api/test"
    assert _betboom_url("https://api.betboom.ru/v1/action?x=1") == "https://api.betboom.ru/v1/action"
    assert _betboom_url("https://example.com/api/test") == ""
    safe = _safe_response_body(
        '{"success":false,"error":{"message":"denied","token":"secret"},"session":"hidden"}',
        "application/json",
    )
    encoded = json.dumps(safe, ensure_ascii=False)
    assert "denied" in encoded
    assert "secret" not in encoded
    assert "hidden" not in encoded
    print("BetBoom network diagnostics self-test passed")


if __name__ == "__main__":
    self_test()
