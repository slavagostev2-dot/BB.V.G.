from __future__ import annotations

import os
import re
from typing import Any

import betboom_auto_participation as auto

CLICK_RE = re.compile(r"(?:принять\s+участие|участвовать|участвую)", re.IGNORECASE)
SUCCESS_RE = auto._SUCCESS_RE


def _text(page: Any) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return ""


def _success(page: Any) -> bool:
    return bool(SUCCESS_RE.search(_text(page)))


def _click_candidates(page: Any, timeout_ms: int) -> tuple[bool, str]:
    """Click a visible participation control across semantic and SPA wrappers."""

    selectors = (
        page.get_by_role("button", name=CLICK_RE),
        page.locator("button").filter(has_text=CLICK_RE),
        page.locator('[role="button"]').filter(has_text=CLICK_RE),
        page.locator("a").filter(has_text=CLICK_RE),
        page.locator("div").filter(has_text=CLICK_RE),
        page.locator("span").filter(has_text=CLICK_RE),
        page.get_by_text(CLICK_RE),
    )
    for locator in selectors:
        try:
            count = min(locator.count(), 20)
        except Exception:
            continue
        for index in range(count):
            try:
                candidate = locator.nth(index)
                if not candidate.is_visible():
                    continue
                label = re.sub(
                    r"\s+", " ", candidate.inner_text(timeout=1500)
                ).strip()[:120]
                candidate.click(timeout=timeout_ms, force=True)
                return True, label or "playwright_locator"
            except Exception:
                continue

    try:
        result = page.evaluate(
            """
            () => {
              const re = /(принять\s+участие|участвовать|участвую)/i;
              const nodes = Array.from(document.querySelectorAll(
                'button,[role="button"],a,div,span'
              ));
              for (const el of nodes) {
                const text = (el.innerText || el.textContent || '')
                  .replace(/\s+/g, ' ').trim();
                if (!text || !re.test(text)) continue;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (
                  style.visibility === 'hidden' ||
                  style.display === 'none' ||
                  rect.width <= 0 ||
                  rect.height <= 0
                ) continue;
                el.click();
                return text.slice(0, 120);
              }
              return '';
            }
            """
        )
        if result:
            return True, str(result)
    except Exception:
        pass
    return False, ""


def participate(url: str) -> auto.ParticipationResult:
    """Use the stored BetBoom browser session as a resilient participation fallback."""

    if not url.startswith("https://betboom.ru/freestream/"):
        return auto.ParticipationResult(False, "invalid_url", "некорректная ссылка BetBoom")

    storage_state = auto._storage_state()
    if storage_state is None:
        return auto.ParticipationResult(False, "not_configured", "сессия BetBoom не настроена")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return auto.ParticipationResult(False, "dependency_missing", "Playwright не установлен")

    timeout_ms = max(
        10000,
        min(60000, int(os.getenv("BETBOOM_PARTICIPATION_TIMEOUT_MS", "30000"))),
    )
    channel = os.getenv("BETBOOM_BROWSER_CHANNEL", "chrome").strip() or "chrome"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel=channel)
            context = browser.new_context(storage_state=storage_state)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3500)

            if _success(page):
                browser.close()
                return auto.ParticipationResult(
                    True,
                    "already_participating",
                    "BetBoom уже показывает подтверждённое участие",
                )

            clicked, _ = _click_candidates(page, timeout_ms)
            if not clicked:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(3500)
                if _success(page):
                    browser.close()
                    return auto.ParticipationResult(
                        True,
                        "already_participating",
                        "BetBoom подтвердил участие после повторной загрузки",
                    )
                clicked, _ = _click_candidates(page, timeout_ms)

            if not clicked:
                body = _text(page).casefold()
                detail = (
                    "страница показывает вход/авторизацию"
                    if any(value in body for value in ("войти", "авторизоваться", "авторизация"))
                    else "кнопка участия не найдена после расширенного поиска"
                )
                browser.close()
                return auto.ParticipationResult(False, "button_not_found", detail)

            for _ in range(4):
                page.wait_for_timeout(1500)
                if _success(page):
                    browser.close()
                    return auto.ParticipationResult(
                        True,
                        "participated",
                        "BetBoom подтвердил участие после нажатия",
                    )

            try:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2500)
            except Exception:
                pass
            if _success(page):
                browser.close()
                return auto.ParticipationResult(
                    True,
                    "participated",
                    "BetBoom подтвердил участие после контрольной перезагрузки",
                )

            browser.close()
            return auto.ParticipationResult(
                False,
                "unconfirmed",
                "элемент участия нажат, но подтверждение BetBoom не найдено",
            )
    except Exception as exc:
        return auto.ParticipationResult(
            False,
            "browser_error",
            f"{type(exc).__name__}: {exc}"[:300],
        )
