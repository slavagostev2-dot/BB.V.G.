from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import betboom_auto_participation as auto

TRIGGER_PATH = Path(__file__).with_name("force_auto_participation.trigger")
RESULT_PATH = Path(__file__).with_name("force_auto_participation_result.json")
CLICK_RE = re.compile(r"(?:принять\s+участие|участвовать|участвую)", re.IGNORECASE)
SUCCESS_RE = auto._SUCCESS_RE


def _text(page: Any) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return ""


def _write(payload: dict[str, Any]) -> None:
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _success(page: Any) -> bool:
    return bool(SUCCESS_RE.search(_text(page)))


def _click_candidates(page: Any, timeout_ms: int) -> tuple[bool, str]:
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
                label = re.sub(r"\s+", " ", candidate.inner_text(timeout=1500)).strip()[:120]
                candidate.click(timeout=timeout_ms, force=True)
                return True, label or "playwright_locator"
            except Exception:
                continue

    try:
        result = page.evaluate(
            """
            () => {
              const re = /(принять\\s+участие|участвовать|участвую)/i;
              const nodes = Array.from(document.querySelectorAll('button,[role="button"],a,div,span'));
              for (const el of nodes) {
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!text || !re.test(text)) continue;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (style.visibility === 'hidden' || style.display === 'none' || rect.width <= 0 || rect.height <= 0) continue;
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


def main() -> int:
    url = TRIGGER_PATH.read_text(encoding="utf-8").strip()
    attempted_at = datetime.now(timezone.utc).isoformat()
    if not url.startswith("https://betboom.ru/freestream/"):
        _write({"url": url, "success": False, "status": "invalid_url", "attempted_at": attempted_at})
        return 1

    storage_state = auto._storage_state()
    if storage_state is None:
        _write({"url": url, "success": False, "status": "not_configured", "attempted_at": attempted_at})
        return 1

    from playwright.sync_api import sync_playwright

    timeout_ms = max(10000, min(60000, int(os.getenv("BETBOOM_PARTICIPATION_TIMEOUT_MS", "30000"))))
    channel = os.getenv("BETBOOM_BROWSER_CHANNEL", "chrome").strip() or "chrome"
    payload: dict[str, Any] = {"url": url, "attempted_at": attempted_at}

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
                payload.update(success=True, status="already_participating", detail="BetBoom уже показывает подтверждённое участие")
                browser.close()
                _write(payload)
                return 0

            clicked, label = _click_candidates(page, timeout_ms)
            if not clicked:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(3500)
                if _success(page):
                    payload.update(success=True, status="already_participating", detail="BetBoom подтвердил участие после повторной загрузки")
                    browser.close()
                    _write(payload)
                    return 0
                clicked, label = _click_candidates(page, timeout_ms)

            if not clicked:
                body = _text(page).casefold()
                hint = "authorization" if any(x in body for x in ("войти", "авторизоваться", "авторизация")) else "control_not_found"
                payload.update(success=False, status="button_not_found", detail=hint)
                browser.close()
                _write(payload)
                return 1

            payload["clicked_label"] = label
            for _ in range(4):
                page.wait_for_timeout(1500)
                if _success(page):
                    payload.update(success=True, status="participated", detail="BetBoom подтвердил участие после нажатия")
                    browser.close()
                    _write(payload)
                    return 0

            try:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2500)
            except Exception:
                pass
            if _success(page):
                payload.update(success=True, status="participated", detail="BetBoom подтвердил участие после контрольной перезагрузки")
                browser.close()
                _write(payload)
                return 0

            payload.update(success=False, status="unconfirmed", detail="Элемент участия нажат, но подтверждение BetBoom не найдено")
            browser.close()
            _write(payload)
            return 1
    except Exception as exc:
        payload.update(success=False, status="browser_error", detail=f"{type(exc).__name__}: {exc}"[:300])
        _write(payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
