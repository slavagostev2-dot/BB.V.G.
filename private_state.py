from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parent
DEPLOYMENT_PATH = ROOT / "private_state_deployment.json"
LEGACY_ACCESS_PATH = ROOT / "bot_access.json"
LEGACY_REQUESTS_PATH = ROOT / "source_requests.json"
DEFAULT_TIMEOUT = max(5, int(os.getenv("PRIVATE_STATE_TIMEOUT_SECONDS", "20")))


def _deployment() -> dict[str, Any]:
    try:
        value = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def api_url() -> str:
    configured_url = str(os.getenv("BBVG_STATE_API_URL", "")).strip().rstrip("/")
    if configured_url:
        return configured_url
    value = _deployment()
    if str(value.get("status") or "") != "deployed":
        return ""
    return str(value.get("url") or "").strip().rstrip("/")


def api_token() -> str:
    return str(os.getenv("BBVG_STATE_API_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()


def configured() -> bool:
    return bool(api_url() and api_token())


def _request_json(path: str, *, method: str = "GET", payload: Any = None) -> Any:
    base = api_url()
    token = api_token()
    if not base or not token:
        raise RuntimeError("Private state API is not configured")
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "BBVG/1.0 private-state-client",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Private state API HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Private state API unavailable: {exc.reason}") from exc
    value = json.loads(raw) if raw else {}
    if isinstance(value, dict) and value.get("ok") is False:
        raise RuntimeError(str(value.get("error") or "Private state API rejected request"))
    return value


def _read_legacy(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def load_access(default: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    fallback = default or {}
    if configured():
        value = _request_json("/v1/admin/access")
        return (value if isinstance(value, dict) else dict(fallback)), True
    value = _read_legacy(LEGACY_ACCESS_PATH, fallback)
    has_private_records = bool(
        value.get("users")
        or value.get("owner_id")
        or value.get("admins")
        or value.get("notification_recipients")
    )
    return value, has_private_records


def save_access(value: dict[str, Any]) -> None:
    if not configured():
        raise RuntimeError("Private state API is required for saving access data")
    _request_json("/v1/admin/access", method="PUT", payload=value)


def load_source_requests(default: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = default or {"version": 1, "requests": {}}
    if configured():
        value = _request_json("/v1/admin/source-requests")
        return value if isinstance(value, dict) else dict(fallback)
    return _read_legacy(LEGACY_REQUESTS_PATH, fallback)


def save_source_requests(value: dict[str, Any]) -> None:
    if not configured():
        raise RuntimeError("Private state API is required for saving source requests")
    _request_json("/v1/admin/source-requests", method="PUT", payload=value)
