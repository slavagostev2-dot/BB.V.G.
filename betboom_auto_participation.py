from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any


_SUCCESS_RE = re.compile(
    r"(?:участие\s+(?:принято|подтверждено|зарегистрировано)|"
    r"вы\s+(?:уже\s+)?участвуете|уже\s+участвуете|участие\s+отмечено)",
    re.IGNORECASE,
)
_BUTTON_RE = re.compile(r"^(?:участвую|принять\s+участие)$", re.IGNORECASE)


@dataclass(frozen=True)
class ParticipationResult:
    success: bool
    status: str
    detail: str
    
def _event_token(normalized: str, entry: dict[str, Any]) -> str:
    return str(
        entry.get("identifier")
        or entry.get("message_id")
        or normalized
    ).strip().casefold()


def _eligible_for_event_attempt(
    entry: dict[str, Any],
    monitor: Any,
    current: Any,
) -> bool:
    url = str(entry.get("url") or "").strip()
    if not url:
        return False

    if str(entry.get("verification_status") or "") == monitor.WHEEL_VERIFICATION_FAILED:
        return False

    available_at = monitor.parse_datetime(entry.get("available_at"))
    if available_at is not None and available_at > current:
        return False

    return True

def enabled() -> bool:
    return os.getenv("BETBOOM_AUTO_PARTICIPATE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _storage_state() -> dict[str, Any] | None:
    raw = os.getenv("BETBOOM_STORAGE_STATE_JSON", "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def configured() -> bool:
    return enabled() and _storage_state() is not None


def _body_text(page: Any) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return ""


def participate(url: str) -> ParticipationResult:
    """Open one BetBoom wheel in an authenticated browser and click participation.

    The function is deliberately fail-closed: it reports success only when the
    resulting page contains an explicit participation confirmation. A click by
    itself is never enough to mark a wheel as participated in the monitor.
    """

    if not enabled():
        return ParticipationResult(False, "disabled", "автоучастие отключено")

    storage_state = _storage_state()
    if storage_state is None:
        return ParticipationResult(
            False,
            "not_configured",
            "не задан корректный BETBOOM_STORAGE_STATE_JSON",
        )

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ParticipationResult(
            False,
            "dependency_missing",
            "Playwright не установлен",
        )

    timeout_ms = max(
        5000,
        min(60000, int(os.getenv("BETBOOM_PARTICIPATION_TIMEOUT_MS", "20000"))),
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(storage_state=storage_state)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            before = _body_text(page)
            if _SUCCESS_RE.search(before):
                browser.close()
                return ParticipationResult(
                    True,
                    "already_participating",
                    "BetBoom уже показывает подтверждённое участие",
                )

            buttons = page.get_by_role("button", name=_BUTTON_RE)
            if buttons.count() == 0:
                browser.close()
                return ParticipationResult(
                    False,
                    "button_not_found",
                    "кнопка «Участвую»/«Принять участие» не найдена",
                )

            buttons.first.click(timeout=timeout_ms)
            try:
                page.wait_for_function(
                    """() => /участие\\s+(принято|подтверждено|зарегистрировано)|вы\\s+(уже\\s+)?участвуете|уже\\s+участвуете|участие\\s+отмечено/i.test(document.body?.innerText || '')""",
                    timeout=timeout_ms,
                )
            except PlaywrightTimeoutError:
                pass

            after = _body_text(page)
            browser.close()
            if _SUCCESS_RE.search(after):
                return ParticipationResult(
                    True,
                    "participated",
                    "BetBoom подтвердил участие после нажатия кнопки",
                )
            return ParticipationResult(
                False,
                "unconfirmed",
                "кнопка нажата, но подтверждение участия на странице не найдено",
            )
    except Exception as exc:
        return ParticipationResult(
            False,
            "browser_error",
            f"{type(exc).__name__}: {exc}"[:300],
        )


def process_active_wheels(state: dict[str, Any], monitor: Any) -> dict[str, int | bool]:
    """Attempt participation for every currently active, not-yet-participated wheel."""

    if not configured():
        return {"changed": False, "attempted": 0, "succeeded": 0, "failed": 0}

    current = monitor.now_utc()
    retry_minutes = max(
        1,
        min(1440, int(os.getenv("BETBOOM_PARTICIPATION_RETRY_MINUTES", "10"))),
    )
    retry_delta = timedelta(minutes=retry_minutes)
    attempts = state.setdefault("auto_participation_attempts", {})
    changed = False
    attempted = 0
    succeeded = 0
    failed = 0

    for key, entry in list(state.setdefault("active_wheels", {}).items()):
        if not isinstance(entry, dict):
            continue
        normalized = str(key).casefold()
        if monitor.is_participating(state, normalized):
            continue

        url = str(entry.get("url") or "").strip()
        if not url:
            continue

        available_at = monitor.parse_datetime(entry.get("available_at"))
        if available_at is not None and available_at > current:
            continue
        if str(entry.get("verification_status") or "") == monitor.WHEEL_VERIFICATION_FAILED:
            continue

        previous = attempts.get(normalized)
        if isinstance(previous, dict):
            previous_at = monitor.parse_datetime(previous.get("attempted_at"))
            if previous_at is not None and current - previous_at < retry_delta:
                continue

        attempted += 1
        result = participate(url)
        attempts[normalized] = {
            "attempted_at": current.isoformat(),
            "status": result.status,
            "detail": result.detail[:300],
        }
        entry["auto_participation_status"] = result.status
        entry["auto_participation_checked_at"] = current.isoformat()
        changed = True

        if not result.success:
            failed += 1
            entry["auto_participation_error"] = result.detail[:300]
            continue

        context = {
            "wheel_key": normalized,
            "identifier": str(entry.get("identifier") or normalized),
            "url": url,
            "source": str(entry.get("source") or ""),
            "message_id": entry.get("message_id", 0),
            "message_date": entry.get("message_date"),
            "message_url": entry.get("message_url"),
            "message_text": entry.get("message_text"),
            "status": entry.get("status"),
            "method": "автоматическое участие подтверждено BetBoom",
            "created_at": current.isoformat(),
        }
        monitor.mark_participating(state, context)
        participant = state.setdefault("participating_wheels", {}).get(normalized)
        if isinstance(participant, dict):
            participant["participation_source"] = "betboom_browser"
            participant["participation_status"] = result.status
            participant["confirmed_at"] = current.isoformat()
        entry.pop("auto_participation_error", None)
        entry["auto_participation_confirmed_at"] = current.isoformat()
        succeeded += 1

    return {
        "changed": changed,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
    }
