from __future__ import annotations

import os
import re
from typing import Any

import betboom_auto_participation as auto


CLICK_RE = re.compile(
    r"(?:принять\s+участие|участвовать|участвую)",
    re.IGNORECASE,
)
SUCCESS_LABEL_RE = re.compile(
    r"(?:участие\s+(?:принято|подтверждено|зарегистрировано|отмечено)|"
    r"вы\s+(?:уже\s+)?участвуете(?:\s+в\s+розыгрыше)?|"
    r"уже\s+участвуете(?:\s+в\s+розыгрыше)?|"
    r"теперь\s+ты\s+участвуешь\s+в\s+розыгрыше|вы\s+в\s+розыгрыше)"
    r"[.!]?",
    re.IGNORECASE,
)


def _normalized_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _matches_full_label(pattern: re.Pattern[str], value: object) -> bool:
    return bool(pattern.fullmatch(_normalized_label(value)))


def _text(page: Any) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return ""


def _matching_visible_label(
    locator: Any,
    pattern: re.Pattern[str],
    *,
    limit: int = 50,
) -> tuple[Any | None, str]:
    try:
        count = min(locator.count(), limit)
    except Exception:
        return None, ""
    for index in range(count):
        try:
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            label = _normalized_label(candidate.inner_text(timeout=1500))
            if _matches_full_label(pattern, label):
                return candidate, label[:120]
        except Exception:
            continue
    return None, ""


def _success(page: Any) -> bool:
    """Accept only a visible, self-contained confirmation label.

    Searching the entire body is unsafe because wheel rules and help text may
    contain phrases such as ``если вы участвуете`` without confirming the
    current account's participation in the current wheel.
    """

    try:
        locators = (
            page.get_by_text(SUCCESS_LABEL_RE),
            page.locator('[role="status"],[aria-live]').filter(
                has_text=SUCCESS_LABEL_RE
            ),
        )
    except Exception:
        return False
    return any(
        _matching_visible_label(locator, SUCCESS_LABEL_RE)[0] is not None
        for locator in locators
    )


def _click_candidates(page: Any, timeout_ms: int) -> tuple[bool, str]:
    """Click only a visible control whose complete label is a participation verb."""

    selectors = (
        page.get_by_role("button", name=CLICK_RE),
        page.locator("button").filter(has_text=CLICK_RE),
        page.locator('[role="button"]').filter(has_text=CLICK_RE),
        page.locator("a").filter(has_text=CLICK_RE),
        page.get_by_text(CLICK_RE),
    )
    for locator in selectors:
        candidate, label = _matching_visible_label(locator, CLICK_RE)
        if candidate is None:
            continue
        try:
            candidate.click(timeout=timeout_ms, force=True)
            return True, label or "playwright_locator"
        except Exception:
            continue

    try:
        result = page.evaluate(
            r"""
            () => {
              const re = /^(принять\s+участие|участвовать|участвую)[.!]?$/i;
              const nodes = Array.from(document.querySelectorAll(
                'button,[role="button"],a,div,span'
              ));
              const candidates = [];
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
                candidates.push({el, text, area: rect.width * rect.height});
              }
              candidates.sort((a, b) => a.area - b.area);
              for (const item of candidates) {
                const target = item.el.closest('button,[role="button"],a') || item.el;
                try {
                  target.click();
                  return item.text.slice(0, 120);
                } catch (_) {}
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


def _diagnostic_labels(page: Any) -> str:
    """Return short visible clickable labels without storing page contents."""

    try:
        locator = page.locator('button,[role="button"],a')
        count = min(locator.count(), 40)
    except Exception:
        return ""
    labels: list[str] = []
    seen: set[str] = set()
    for index in range(count):
        try:
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            label = _normalized_label(candidate.inner_text(timeout=1000))
        except Exception:
            continue
        if not label or len(label) > 80:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= 8:
            break
    return " | ".join(labels)[:220]


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
                    "BetBoom уже показывает точное подтверждение участия",
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
                        "BetBoom показывает точное подтверждение после повторной загрузки",
                    )
                clicked, _ = _click_candidates(page, timeout_ms)

            if not clicked:
                body = _text(page).casefold()
                labels = _diagnostic_labels(page)
                detail = (
                    "страница показывает вход/авторизацию"
                    if any(value in body for value in ("войти", "авторизоваться", "авторизация"))
                    else "кнопка участия не найдена после точного поиска"
                )
                if labels:
                    detail += f"; видимые действия: {labels}"
                browser.close()
                return auto.ParticipationResult(False, "button_not_found", detail[:300])

            for _ in range(4):
                page.wait_for_timeout(1500)
                if _success(page):
                    browser.close()
                    return auto.ParticipationResult(
                        True,
                        "participated",
                        "BetBoom показал точное подтверждение после нажатия",
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
                    "BetBoom показал точное подтверждение после контрольной перезагрузки",
                )

            browser.close()
            return auto.ParticipationResult(
                False,
                "unconfirmed",
                "элемент участия нажат, но точное подтверждение BetBoom не найдено",
            )
    except Exception as exc:
        return auto.ParticipationResult(
            False,
            "browser_error",
            f"{type(exc).__name__}: {exc}"[:300],
        )


def self_test() -> None:
    assert _matches_full_label(CLICK_RE, "Участвовать")
    assert _matches_full_label(CLICK_RE, "  Принять   участие  ")
    assert not _matches_full_label(
        CLICK_RE,
        "В розыгрыше могут участвовать все зарегистрированные пользователи",
    )
    assert _matches_full_label(SUCCESS_LABEL_RE, "Вы уже участвуете")
    assert _matches_full_label(SUCCESS_LABEL_RE, "Вы уже участвуете в розыгрыше!")
    assert not _matches_full_label(
        SUCCESS_LABEL_RE,
        "Если вы участвуете, дождитесь окончания таймера",
    )
    print("BetBoom exact participation controls self-test passed")


if __name__ == "__main__":
    self_test()
