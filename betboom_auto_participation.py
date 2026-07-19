from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


_SUCCESS_RE = re.compile(
    r"(?:участие\s+(?:принято|подтверждено|зарегистрировано)|"
    r"вы\s+(?:уже\s+)?участвуете|уже\s+участвуете|участие\s+отмечено)",
    re.IGNORECASE,
)
_BUTTON_RE = re.compile(
    r"^\s*(?:участвую|участвовать|принять\s+участие)\s*$",
    re.IGNORECASE,
)
_DEFAULT_ALERT_USER = "Вячеслав"


@dataclass(frozen=True)
class ParticipationResult:
    success: bool
    status: str
    detail: str


def enabled() -> bool:
    return os.getenv("BETBOOM_AUTO_PARTICIPATE", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _storage_state_raw() -> str:
    direct = os.getenv("BETBOOM_STORAGE_STATE_JSON", "").strip()
    if direct:
        return direct
    part1 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART1", "")
    part2 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART2", "")
    if not part1 and not part2:
        return ""
    return part1 + part2


def _storage_state() -> dict[str, Any] | None:
    raw = _storage_state_raw()
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
    if not enabled():
        return ParticipationResult(False, "disabled", "автоучастие отключено")

    storage_state = _storage_state()
    if storage_state is None:
        return ParticipationResult(False, "not_configured", "не задан storage state")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ParticipationResult(False, "dependency_missing", "Playwright не установлен")

    timeout_ms = max(5000, min(60000, int(os.getenv("BETBOOM_PARTICIPATION_TIMEOUT_MS", "20000"))))
    browser_channel = os.getenv("BETBOOM_BROWSER_CHANNEL", "chrome").strip() or "chrome"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel=browser_channel)
            context = browser.new_context(storage_state=storage_state)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            before = _body_text(page)
            if _SUCCESS_RE.search(before):
                browser.close()
                return ParticipationResult(True, "already_participating", "BetBoom уже показывает подтверждённое участие")

            buttons = page.locator("button").filter(
                has_text=re.compile(
                    r"^\s*(?:участвую|участвовать|принять\s+участие)\s*$",
                    re.IGNORECASE,
                )
            )
            if buttons.count() == 0:
                browser.close()
                return ParticipationResult(False, "button_not_found", "кнопка участия не найдена")

            buttons.first.scroll_into_view_if_needed()
            buttons.first.click(timeout=timeout_ms)

            try:
                page.wait_for_function(
                    """() => /участие\s+(принято|подтверждено|зарегистрировано)|вы\s+(уже\s+)?участвуете|уже\s+участвуете|участие\s+отмечено/i.test(document.body?.innerText || '')""",
                    timeout=timeout_ms,
                )
            except PlaywrightTimeoutError:
                pass

            after = _body_text(page)
            browser.close()
            if _SUCCESS_RE.search(after):
                return ParticipationResult(True, "participated", "BetBoom подтвердил участие после нажатия кнопки")
            return ParticipationResult(False, "unconfirmed", "кнопка нажата, но подтверждение участия не найдено")
    except Exception as exc:
        return ParticipationResult(False, "browser_error", f"{type(exc).__name__}: {exc}"[:300])


# остальная логика обработки событий сохраняется
