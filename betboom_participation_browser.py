from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

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
COOKIE_RE = re.compile(
    r"(?:окей|понятно|согласен|принять(?:\s+все)?|разрешить\s+все)",
    re.IGNORECASE,
)
PROMO_DETAILS_RE = re.compile(r"об\s+акции", re.IGNORECASE)


def _normalized_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _matches_full_label(pattern: re.Pattern[str], value: object) -> bool:
    return bool(pattern.fullmatch(_normalized_label(value)))


def _search_roots(page: Any) -> list[Any]:
    """Return the main document followed by every attached child frame."""

    roots: list[Any] = [page]
    try:
        main_frame = page.main_frame
        frames = list(page.frames)
    except Exception:
        return roots
    for frame in frames:
        if frame is main_frame or frame in roots:
            continue
        roots.append(frame)
    return roots


def _root_name(root: Any, page: Any) -> str:
    if root is page:
        return "main"
    try:
        parsed = urlparse(str(getattr(root, "url", "") or ""))
        return f"frame:{parsed.netloc or parsed.path[:40] or 'unknown'}"
    except Exception:
        return "frame:unknown"


def _text(root: Any) -> str:
    try:
        return str(root.locator("body").inner_text(timeout=3000) or "")
    except Exception:
        return ""


def _all_text(page: Any) -> str:
    return "\n".join(
        value for value in (_text(root) for root in _search_roots(page)) if value
    )


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
            label = _normalized_label(candidate.inner_text(timeout=1000))
            if _matches_full_label(pattern, label):
                return candidate, label[:120]
        except Exception:
            continue
    return None, ""


def _visible_exact_control(root: Any, pattern: re.Pattern[str]) -> tuple[Any | None, str]:
    try:
        selectors = (
            root.get_by_role("button", name=pattern),
            root.locator("button").filter(has_text=pattern),
            root.locator('[role="button"]').filter(has_text=pattern),
            root.locator("a").filter(has_text=pattern),
            root.get_by_text(pattern),
        )
    except Exception:
        return None, ""
    for locator in selectors:
        candidate, label = _matching_visible_label(locator, pattern)
        if candidate is not None:
            return candidate, label
    return None, ""


def _visible_control_location(page: Any, pattern: re.Pattern[str]) -> str:
    for root in _search_roots(page):
        candidate, label = _visible_exact_control(root, pattern)
        if candidate is not None:
            return f"{_root_name(root, page)}:{label}"[:180]
    return ""


def _success(page: Any) -> bool:
    """Accept only a visible, self-contained confirmation label in any frame."""

    for root in _search_roots(page):
        try:
            locators = (
                root.get_by_text(SUCCESS_LABEL_RE),
                root.locator('[role="status"],[aria-live]').filter(
                    has_text=SUCCESS_LABEL_RE
                ),
            )
        except Exception:
            continue
        if any(
            _matching_visible_label(locator, SUCCESS_LABEL_RE)[0] is not None
            for locator in locators
        ):
            return True
    return False


def _click_in_root(root: Any, timeout_ms: int) -> tuple[bool, str]:
    candidate, label = _visible_exact_control(root, CLICK_RE)
    if candidate is not None:
        try:
            candidate.click(timeout=min(timeout_ms, 5000), force=True)
            return True, label or "playwright_locator"
        except Exception:
            pass

    try:
        result = root.evaluate(
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


def _click_candidates(page: Any, timeout_ms: int) -> tuple[bool, str]:
    """Click an exact participation control in the main page or any child frame."""

    for root in _search_roots(page):
        clicked, label = _click_in_root(root, timeout_ms)
        if clicked:
            return True, f"{_root_name(root, page)}:{label}"[:180]
    return False, ""


def _click_preparation_control(
    page: Any,
    pattern: re.Pattern[str],
    timeout_ms: int,
) -> str:
    """Click one exact harmless preparation control across page and frames."""

    for root in _search_roots(page):
        candidate, label = _visible_exact_control(root, pattern)
        if candidate is None:
            continue
        try:
            candidate.click(timeout=min(timeout_ms, 4000), force=True)
            return f"{_root_name(root, page)}:{label}"[:160]
        except Exception:
            continue
    return ""


def _preparation_patterns() -> tuple[re.Pattern[str], ...]:
    """Only consent controls are safe before participation.

    «Об акции» is a post-click BetBoom state and must never be clicked as preparation.
    """

    return (COOKIE_RE,)


def _prepare_page(page: Any, timeout_ms: int) -> list[str]:
    """Dismiss consent without touching the promotion state."""

    actions: list[str] = []
    for pattern in _preparation_patterns():
        location = _click_preparation_control(page, pattern, timeout_ms)
        if not location:
            continue
        actions.append(location)
        try:
            page.wait_for_timeout(350)
        except Exception:
            pass
    return actions


def _accepted_post_click_layout(
    *,
    participation_visible: bool,
    promo_details_visible: bool,
) -> bool:
    """Recognize the BetBoom layout that replaces participation after a click."""

    return promo_details_visible and not participation_visible


def _post_click_confirmed(page: Any) -> tuple[bool, str]:
    """Confirm participation after our click without opening «Об акции»."""

    if _success(page):
        return True, "exact_success_label"
    participation_location = _visible_control_location(page, CLICK_RE)
    promo_location = _visible_control_location(page, PROMO_DETAILS_RE)
    if _accepted_post_click_layout(
        participation_visible=bool(participation_location),
        promo_details_visible=bool(promo_location),
    ):
        return True, f"post_click_layout:{promo_location}"[:180]
    return False, ""


def _find_and_click(page: Any, timeout_ms: int) -> tuple[bool, str, list[str]]:
    """Dismiss consent, then seek the real participation button only."""

    preparations: list[str] = []
    for _ in range(8):
        if _success(page):
            return False, "already_participating", preparations
        for item in _prepare_page(page, timeout_ms):
            if item not in preparations:
                preparations.append(item)
        if _success(page):
            return False, "already_participating", preparations
        clicked, location = _click_candidates(page, timeout_ms)
        if clicked:
            return True, location, preparations
        try:
            page.wait_for_timeout(500)
        except Exception:
            break
    return False, "", preparations


def _diagnostic_labels(page: Any) -> str:
    """Return short visible clickable labels and frame locations, without page contents."""

    labels: list[str] = []
    seen: set[str] = set()
    for root in _search_roots(page):
        try:
            locator = root.locator('button,[role="button"],a')
            count = min(locator.count(), 40)
        except Exception:
            continue
        prefix = _root_name(root, page)
        for index in range(count):
            try:
                candidate = locator.nth(index)
                if not candidate.is_visible():
                    continue
                label = _normalized_label(candidate.inner_text(timeout=800))
            except Exception:
                continue
            if not label or len(label) > 80:
                continue
            rendered = f"{prefix}:{label}"
            key = rendered.casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(rendered)
            if len(labels) >= 12:
                return " | ".join(labels)[:260]
    if len(_search_roots(page)) > 1 and not labels:
        labels.append(f"frames:{len(_search_roots(page)) - 1}")
    return " | ".join(labels)[:260]


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
        8000,
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
            page.wait_for_timeout(800)

            clicked, location, preparations = _find_and_click(page, timeout_ms)
            if location == "already_participating":
                browser.close()
                return auto.ParticipationResult(
                    True,
                    "already_participating",
                    "BetBoom уже показывает точное подтверждение участия",
                )

            if not clicked:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                clicked, location, retried_preparations = _find_and_click(page, timeout_ms)
                for item in retried_preparations:
                    if item not in preparations:
                        preparations.append(item)
                if location == "already_participating":
                    browser.close()
                    return auto.ParticipationResult(
                        True,
                        "already_participating",
                        "BetBoom показывает точное подтверждение после повторной загрузки",
                    )

            if not clicked:
                body = _all_text(page).casefold()
                labels = _diagnostic_labels(page)
                detail = (
                    "страница показывает вход/авторизацию"
                    if any(value in body for value in ("войти", "авторизоваться", "авторизация"))
                    else "кнопка участия не найдена после закрытия cookie"
                )
                if preparations:
                    detail += "; подготовка: " + " | ".join(preparations)
                if labels:
                    detail += f"; видимые действия: {labels}"
                browser.close()
                return auto.ParticipationResult(False, "button_not_found", detail[:300])

            for _ in range(8):
                page.wait_for_timeout(500)
                confirmed, confirmation = _post_click_confirmed(page)
                if confirmed:
                    browser.close()
                    detail = (
                        f"BetBoom подтвердил участие после нажатия ({location}; {confirmation})"
                    )
                    if preparations:
                        detail += "; подготовка: " + " | ".join(preparations)
                    return auto.ParticipationResult(True, "participated", detail[:300])

            try:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1000)
                _prepare_page(page, timeout_ms)
            except Exception:
                pass
            confirmed, confirmation = _post_click_confirmed(page)
            if confirmed:
                browser.close()
                return auto.ParticipationResult(
                    True,
                    "participated",
                    (
                        "BetBoom подтвердил участие после контрольной перезагрузки "
                        f"({location}; {confirmation})"
                    )[:300],
                )

            browser.close()
            return auto.ParticipationResult(
                False,
                "unconfirmed",
                f"элемент участия нажат ({location}), но состояние после нажатия не распознано"[:300],
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
    assert _matches_full_label(COOKIE_RE, "Окей")
    assert _matches_full_label(PROMO_DETAILS_RE, "Об акции")
    assert not _matches_full_label(PROMO_DETAILS_RE, "Другие акции")
    assert PROMO_DETAILS_RE not in _preparation_patterns()
    assert _accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
    )
    assert not _accepted_post_click_layout(
        participation_visible=True,
        promo_details_visible=True,
    )
    assert not _accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=False,
    )
    print("BetBoom exact participation controls self-test passed")


if __name__ == "__main__":
    self_test()
