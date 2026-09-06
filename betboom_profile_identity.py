from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import betboom_participation_browser as browser_helpers


DEFAULT_PROBE_URL = "https://betboom.ru/actions/boomstatus"
DEFAULT_CACHE_PATH = Path("/tmp/bbvg-betboom-profile-identities.json")


def _storage_state(part1_name: str, part2_name: str) -> dict[str, Any] | None:
    raw = os.getenv(part1_name, "") + os.getenv(part2_name, "")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def configured_sessions() -> list[tuple[str, dict[str, Any] | None]]:
    return [
        (
            "vyacheslav_primary",
            _storage_state(
                "BETBOOM_STORAGE_STATE_JSON_PART1",
                "BETBOOM_STORAGE_STATE_JSON_PART2",
            ),
        ),
        (
            "vyacheslav_secondary",
            _storage_state(
                "BETBOOM_STORAGE_STATE_JSON_PART3",
                "BETBOOM_STORAGE_STATE_JSON_PART4",
            ),
        ),
        (
            "xflarxx_primary",
            _storage_state(
                "BETBOOM_STORAGE_STATE_JSON_PART5",
                "BETBOOM_STORAGE_STATE_JSON_PART6",
            ),
        ),
    ]


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _session_token_from_value(value: Any) -> str:
    if isinstance(value, dict):
        token = value.get("token")
        if isinstance(token, str) and token.count(".") >= 2:
            return token
        for nested in value.values():
            token = _session_token_from_value(nested)
            if token:
                return token
        return ""
    if isinstance(value, list):
        for nested in value:
            token = _session_token_from_value(nested)
            if token:
                return token
        return ""
    if not isinstance(value, str):
        return ""

    text = value.strip()
    if text.startswith("j:"):
        text = text[2:]
    if text.startswith("{") or text.startswith("["):
        try:
            nested = json.loads(text)
        except json.JSONDecodeError:
            nested = None
        if nested is not None:
            token = _session_token_from_value(nested)
            if token:
                return token
    if text.count(".") >= 2:
        claims = _decode_jwt_payload(text)
        if claims.get("gambler_id") is not None or claims.get("gamblerId") is not None:
            return text
    return ""


def profile_fingerprint_from_next_data(raw: str) -> str:
    """Return an irreversible profile fingerprint without exposing BetBoom IDs."""

    try:
        next_data = json.loads(str(raw or ""))
    except json.JSONDecodeError:
        return ""
    token = _session_token_from_value(next_data)
    if not token:
        return ""
    claims = _decode_jwt_payload(token)
    gambler = claims.get("gambler_id")
    if gambler is None:
        gambler = claims.get("gamblerId")
    if gambler is None or str(gambler).strip() == "":
        return ""
    digest = hashlib.sha256(
        f"bbvg-betboom-profile-v1:{gambler}".encode("utf-8")
    ).hexdigest()
    return digest[:24]


def resolve_profile_fingerprint(
    playwright: Any,
    storage_state: dict[str, Any] | None,
    *,
    url: str = DEFAULT_PROBE_URL,
    timeout_ms: int = 20000,
    channel: str = "chrome",
) -> tuple[str, str]:
    if storage_state is None:
        return "", "not_configured"
    browser = None
    try:
        browser = playwright.chromium.launch(headless=True, channel=channel)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(800)
        auth = browser_helpers._authentication_required(page)
        if auth:
            return "", "authorization_required"
        try:
            raw = page.locator("#__NEXT_DATA__").text_content(timeout=3000) or ""
        except Exception:
            raw = ""
        fingerprint = profile_fingerprint_from_next_data(raw)
        if not fingerprint:
            return "", "identity_unavailable"
        return fingerprint, "ok"
    except Exception as exc:
        return "", f"technical_error:{type(exc).__name__}"
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def build_identity_report() -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "dependency_missing", "accounts": {}}

    timeout_ms = max(
        8000,
        min(45000, int(os.getenv("BETBOOM_IDENTITY_TIMEOUT_MS", "20000"))),
    )
    channel = os.getenv("BETBOOM_BROWSER_CHANNEL", "chrome").strip() or "chrome"
    url = os.getenv("BETBOOM_IDENTITY_PROBE_URL", DEFAULT_PROBE_URL).strip() or DEFAULT_PROBE_URL
    accounts: dict[str, Any] = {}
    with sync_playwright() as playwright:
        for account_key, storage_state in configured_sessions():
            fingerprint, status = resolve_profile_fingerprint(
                playwright,
                storage_state,
                url=url,
                timeout_ms=timeout_ms,
                channel=channel,
            )
            accounts[account_key] = {
                "status": status,
                "profile_fingerprint": fingerprint,
            }

    groups: dict[str, list[str]] = {}
    for account_key, item in accounts.items():
        fingerprint = str(item.get("profile_fingerprint") or "")
        if fingerprint:
            groups.setdefault(fingerprint, []).append(account_key)
    collisions = [sorted(keys) for keys in groups.values() if len(keys) > 1]
    for group in collisions:
        for account_key in group:
            accounts[account_key]["profile_collision_with"] = [
                other for other in group if other != account_key
            ]

    return {
        "status": "collision" if collisions else "ok",
        "accounts": accounts,
        "collision_groups": collisions,
    }


def load_or_build_identity_report(
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[str, Any]:
    try:
        if cache_path.exists():
            value = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("accounts"), dict):
                return value
    except Exception:
        pass
    report = build_identity_report()
    try:
        cache_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    return report


def assert_account_slot_distinct(account_key: str) -> dict[str, Any]:
    """Protect canonical slots without disclosing raw BetBoom account IDs.

    Account 1 is primary. xFLARXx is the explicitly named third slot. The generic
    Account 2 slot must be distinct from both of them. xFLARXx must be distinct
    from Account 1; a collision between Account 2 and xFLARXx invalidates the
    generic Account 2 slot while preserving the explicitly named xFLARXx slot.
    """

    report = load_or_build_identity_report()
    accounts = report.get("accounts")
    if not isinstance(accounts, dict):
        raise RuntimeError("BetBoom profile identity report is unavailable")
    current = accounts.get(account_key)
    if not isinstance(current, dict):
        raise RuntimeError(f"BetBoom profile identity is unavailable for {account_key}")
    if current.get("status") != "ok":
        raise RuntimeError(
            f"BetBoom profile identity check failed for {account_key}: "
            f"{current.get('status')}"
        )
    collisions = {
        str(value)
        for value in current.get("profile_collision_with", [])
        if value
    }
    forbidden: set[str]
    if account_key == "vyacheslav_secondary":
        forbidden = {"vyacheslav_primary", "xflarxx_primary"}
    elif account_key == "xflarxx_primary":
        forbidden = {"vyacheslav_primary"}
    else:
        forbidden = set()
    bad = sorted(collisions & forbidden)
    if bad:
        raise RuntimeError(
            "BetBoom profile collision: "
            f"{account_key} resolves to the same BetBoom profile as {', '.join(bad)}"
        )
    return report


def self_test() -> None:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"gambler_id": 123456}).encode("utf-8")
    ).decode().rstrip("=")
    token = f"{header}.{payload}.signature"
    raw = json.dumps(
        {
            "props": {
                "props": {
                    "session": "j:" + json.dumps({"token": token})
                }
            }
        }
    )
    first = profile_fingerprint_from_next_data(raw)
    second = profile_fingerprint_from_next_data(raw)
    assert first and first == second
    assert "123456" not in first
    assert profile_fingerprint_from_next_data("{}") == ""
    print("BetBoom profile identity guard self-test passed")


if __name__ == "__main__":
    self_test()
