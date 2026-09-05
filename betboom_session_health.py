from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import betboom_participation_browser as browser_helpers

UTC = timezone.utc
DEFAULT_PROBE_URL = "https://betboom.ru/actions/boomstatus"
SESSION_AUTH_RE = re.compile(
    r"(?:войти(?:\s+и\s+участвовать)?|вход|авторизоваться|авторизация)",
    re.IGNORECASE,
)


def _storage_state(part1_name: str, part2_name: str) -> dict[str, Any] | None:
    raw = os.getenv(part1_name, "") + os.getenv(part2_name, "")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def account_sessions() -> list[tuple[str, str, dict[str, Any] | None]]:
    return [
        (
            "vyacheslav_primary",
            "Аккаунт 1",
            _storage_state(
                "BETBOOM_STORAGE_STATE_JSON_PART1",
                "BETBOOM_STORAGE_STATE_JSON_PART2",
            ),
        ),
        (
            "vyacheslav_secondary",
            "Аккаунт 2",
            _storage_state(
                "BETBOOM_STORAGE_STATE_JSON_PART3",
                "BETBOOM_STORAGE_STATE_JSON_PART4",
            ),
        ),
        (
            "xflarxx_primary",
            "xFLARXx",
            _storage_state(
                "BETBOOM_STORAGE_STATE_JSON_PART5",
                "BETBOOM_STORAGE_STATE_JSON_PART6",
            ),
        ),
    ]


def _capture(page: Any, account_key: str, status: str) -> str:
    root = Path(
        os.getenv(
            "BBVG_SESSION_HEALTH_ARTIFACT_DIR",
            str(Path(__file__).resolve().parent / "runtime" / "session_health"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{account_key}-{status}.png"
    try:
        page.screenshot(path=str(target), full_page=True)
    except Exception:
        return ""
    return str(target)


def probe_one(
    playwright: Any,
    *,
    account_key: str,
    account_label: str,
    storage_state: dict[str, Any] | None,
    url: str,
    timeout_ms: int,
    channel: str,
) -> dict[str, Any]:
    base = {
        "account_key": account_key,
        "account_label": account_label,
        "checked_at": datetime.now(UTC).isoformat(),
        "probe_url": url,
    }
    if storage_state is None:
        return {
            **base,
            "status": "not_configured",
            "session_active": False,
            "detail": "storage state отсутствует или JSON повреждён",
        }

    browser = None
    try:
        browser = playwright.chromium.launch(headless=True, channel=channel)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1200)
        auth_location = browser_helpers._visible_control_location(page, SESSION_AUTH_RE)
        final_url = str(getattr(page, "url", "") or "")
        if auth_location:
            artifact = _capture(page, account_key, "authorization_required")
            return {
                **base,
                "status": "authorization_required",
                "session_active": False,
                "detail": f"BetBoom показывает контроль входа: {auth_location}"[:300],
                "final_url": final_url,
                "artifact_url": artifact,
            }

        artifact = _capture(page, account_key, "accepted")
        return {
            **base,
            "status": "session_accepted_by_betboom",
            "session_active": True,
            "detail": (
                "страница с явным login-CTA загрузилась без требования входа; "
                "сохранённая сессия принята BetBoom"
            ),
            "final_url": final_url,
            "artifact_url": artifact,
        }
    except Exception as exc:
        return {
            **base,
            "status": "technical_error",
            "session_active": None,
            "detail": f"{type(exc).__name__}: {exc}"[:300],
        }
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def run() -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "status": "dependency_missing",
            "accounts": [],
        }

    url = os.getenv("BETBOOM_SESSION_HEALTH_URL", DEFAULT_PROBE_URL).strip() or DEFAULT_PROBE_URL
    timeout_ms = max(
        8000,
        min(45000, int(os.getenv("BETBOOM_SESSION_HEALTH_TIMEOUT_MS", "20000"))),
    )
    channel = os.getenv("BETBOOM_BROWSER_CHANNEL", "chrome").strip() or "chrome"

    with sync_playwright() as playwright:
        accounts = [
            probe_one(
                playwright,
                account_key=account_key,
                account_label=account_label,
                storage_state=storage_state,
                url=url,
                timeout_ms=timeout_ms,
                channel=channel,
            )
            for account_key, account_label, storage_state in account_sessions()
        ]

    active_count = sum(item.get("session_active") is True for item in accounts)
    expired_count = sum(item.get("status") == "authorization_required" for item in accounts)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "probe_url": url,
        "active_count": active_count,
        "authorization_required_count": expired_count,
        "accounts": accounts,
    }


def self_test() -> None:
    assert SESSION_AUTH_RE.fullmatch("Войти")
    assert SESSION_AUTH_RE.fullmatch("Войти и участвовать")
    assert not SESSION_AUTH_RE.fullmatch("Участвовать")
    print("BetBoom session health self-test passed")


def main() -> int:
    if os.getenv("BBVG_SESSION_HEALTH_SELF_TEST", "").strip().lower() in {"1", "true"}:
        self_test()
        return 0
    result = run()
    output = Path(os.getenv("BBVG_SESSION_HEALTH_RESULT", "runtime/session_health.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
