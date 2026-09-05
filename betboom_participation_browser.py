from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import betboom_auto_participation as auto

UTC = timezone.utc

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
EMBEDDED_SUCCESS_LABEL_RE = re.compile(
    r"(?:участие\s+(?:принято|подтверждено|зарегистрировано|отмечено)|"
    r"вы\s+уже\s+участвуете(?:\s+в\s+розыгрыше)?|"
    r"уже\s+участвуете(?:\s+в\s+розыгрыше)?|"
    r"теперь\s+ты\s+участвуешь\s+в\s+розыгрыше|вы\s+в\s+розыгрыше)",
    re.IGNORECASE,
)
COOKIE_RE = re.compile(
    r"(?:окей|понятно|согласен|принять(?:\s+все)?|разрешить\s+все)",
    re.IGNORECASE,
)
PROMO_DETAILS_RE = re.compile(r"об\s+акции", re.IGNORECASE)
AUTH_RE = re.compile(
    r"(?:войти|вход|авторизоваться|авторизация)",
    re.IGNORECASE,
)
REFERRAL_INELIGIBLE_LABEL_RE = re.compile(
    r"(?:"
    r"(?:вы|ваш\s+аккаунт|аккаунт)[^.!?\n]{0,100}"
    r"(?:не\s+(?:являетесь|является|подходите|подходит|соответствуете|соответствует))"
    r"[^.!?\n]{0,100}(?:реферал\w*|реферальн\w*\s+услов\w*)"
    r"|"
    r"(?:участие|акция|колесо)[^.!?\n]{0,100}(?:недоступ\w*|закрыт\w*)"
    r"[^.!?\n]{0,140}(?:не\s+реферал\w*|не\s+по\s+реферальн\w*\s+ссылк\w*)"
    r")"
    r"[.!]?",
    re.IGNORECASE,
)


def _normalized_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _matches_full_label(pattern: re.Pattern[str], value: object) -> bool:
    return bool(pattern.fullmatch(_normalized_label(value)))


def _matches_success_label(value: object) -> bool:
    label = _normalized_label(value)
    if _matches_full_label(SUCCESS_LABEL_RE, label):
        return True
    return bool(
        label
        and len(label) <= 500
        and EMBEDDED_SUCCESS_LABEL_RE.search(label)
    )


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


def _authentication_required(page: Any) -> str:
    """Return a visible BetBoom authentication control, if one is present."""

    return _visible_control_location(page, AUTH_RE)


def _visible_referral_ineligible(page: Any) -> str:
    """Return only an explicit, self-contained BetBoom eligibility refusal."""

    for root in _search_roots(page):
        try:
            locators = (
                root.get_by_text(REFERRAL_INELIGIBLE_LABEL_RE),
                root.locator('[role="alert"],[role="status"],[aria-live]').filter(
                    has_text=REFERRAL_INELIGIBLE_LABEL_RE
                ),
            )
        except Exception:
            continue
        for locator in locators:
            _candidate, label = _matching_visible_label(
                locator,
                REFERRAL_INELIGIBLE_LABEL_RE,
            )
            if label:
                return f"{_root_name(root, page)}:{label}"[:220]
    return ""


def _success(page: Any) -> bool:
    """Accept only a visible confirmation while the account is authenticated."""

    if _authentication_required(page):
        return False
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
        for locator in locators:
            try:
                count = min(locator.count(), 50)
            except Exception:
                continue
            for index in range(count):
                try:
                    candidate = locator.nth(index)
                    if not candidate.is_visible():
                        continue
                    if _matches_success_label(candidate.inner_text(timeout=1000)):
                        return True
                except Exception:
                    continue
    return False


def _detail_with_page_hint(page: Any, detail: str) -> str:
    del page
    return detail


def _authorization_failure(
    page: Any,
    url: str,
    detail: str,
) -> auto.ParticipationResult:
    """Return a terminal session-expired result instead of retryable button_not_found."""

    rendered = _detail_with_page_hint(page, detail)
    artifact = _save_diagnostics(
        page,
        url,
        "authorization_required",
        rendered,
    )
    return auto.ParticipationResult(
        False,
        "authorization_required",
        rendered[:300],
        artifact,
    )


def _click_in_root(root: Any, timeout_ms: int) -> tuple[bool, str]:
    """Click only an actionable participation control.

    A forced or synthetic DOM click can report success while BetBoom ignores the
    action behind an overlay or during SPA loading. Let Playwright verify
    actionability so an ignored click remains retryable instead of becoming a
    false participation attempt.
    """

    candidate, label = _visible_exact_control(root, CLICK_RE)
    if candidate is None:
        return False, ""
    try:
        if hasattr(candidate, "is_enabled") and not candidate.is_enabled():
            return False, ""
        candidate.click(timeout=min(timeout_ms, 5000))
        return True, label or "playwright_locator"
    except Exception:
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
    authentication_required: bool = False,
) -> bool:
    """Recognize the BetBoom post-click layout only for an authenticated page."""

    return (
        promo_details_visible
        and not participation_visible
        and not authentication_required
    )


def _post_click_confirmed(page: Any) -> tuple[bool, str]:
    """Confirm participation after our click without opening «Об акции»."""

    auth_location = _authentication_required(page)
    if auth_location:
        return False, f"authentication_required:{auth_location}"[:180]
    if _success(page):
        return True, "exact_success_label"
    participation_location = _visible_control_location(page, CLICK_RE)
    promo_location = _visible_control_location(page, PROMO_DETAILS_RE)
    if _accepted_post_click_layout(
        participation_visible=bool(participation_location),
        promo_details_visible=bool(promo_location),
        authentication_required=False,
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


def _save_diagnostics(
    page: Any,
    url: str,
    status: str,
    detail: str,
) -> str:
    """Persist a screenshot, DOM and non-secret metadata for each failure."""

    if page is None:
        return ""
    root = Path(
        os.getenv(
            "BBVG_BROWSER_ARTIFACT_DIR",
            str(Path(__file__).resolve().parent / "runtime" / "browser_diagnostics"),
        )
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(
        f"{url}\x1f{status}\x1f{stamp}".encode("utf-8")
    ).hexdigest()[:12]
    target = root / f"{stamp}-{digest}"
    try:
        target.mkdir(parents=True, exist_ok=False)
        page.screenshot(path=str(target / "page.png"), full_page=True)
        (target / "page.html").write_text(
            str(page.content() or ""),
            encoding="utf-8",
        )
        (target / "metadata.json").write_text(
            json.dumps(
                {
                    "captured_at": datetime.now(UTC).isoformat(),
                    "url": url,
                    "final_url": str(getattr(page, "url", "") or ""),
                    "status": status,
                    "detail": detail[:1000],
                    "visible_controls": _diagnostic_labels(page),
                    "frame_urls": [
                        str(getattr(frame, "url", "") or "")
                        for frame in _search_roots(page)
                        if frame is not page
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        return ""
    return str(target)


def participate(url: str) -> auto.ParticipationResult:
    """Use the stored BetBoom browser session as a resilient participation fallback."""

    if not url.startswith("https://betboom.ru/freestream/"):
        return auto.ParticipationResult(False, "technical_error", "invalid_url: некорректная ссылка BetBoom")

    storage_state = auto._storage_state()
    if storage_state is None:
        return auto.ParticipationResult(False, "technical_error", "not_configured: сессия BetBoom не настроена")

    page: Any = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return auto.ParticipationResult(False, "technical_error", "dependency_missing: Playwright не установлен")

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

            referral_refusal = _visible_referral_ineligible(page)
            if referral_refusal:
                detail = _detail_with_page_hint(
                    page,
                    f"referral_ineligible_exact_text:{referral_refusal}",
                )
                artifact = _save_diagnostics(
                    page, url, "referral_ineligible", detail
                )
                browser.close()
                return auto.ParticipationResult(
                    False,
                    "referral_ineligible",
                    detail[:300],
                    artifact,
                )

            auth_location = _authentication_required(page)
            if auth_location:
                result = _authorization_failure(
                    page,
                    url,
                    f"страница показывает вход/авторизацию ({auth_location})",
                )
                browser.close()
                return result

            clicked, location, preparations = _find_and_click(page, timeout_ms)
            if location == "already_participating":
                detail = _detail_with_page_hint(
                    page,
                    "BetBoom уже показывает точное подтверждение участия",
                )
                browser.close()
                return auto.ParticipationResult(
                    True,
                    "already_participating",
                    detail[:300],
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
                    detail = _detail_with_page_hint(
                        page,
                        "BetBoom показывает точное подтверждение после повторной загрузки",
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        True,
                        "already_participating",
                        detail[:300],
                    )

            if not clicked:
                referral_refusal = _visible_referral_ineligible(page)
                if referral_refusal:
                    detail = _detail_with_page_hint(
                        page,
                        f"referral_ineligible_exact_text:{referral_refusal}",
                    )
                    artifact = _save_diagnostics(
                        page, url, "referral_ineligible", detail
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        False,
                        "referral_ineligible",
                        detail[:300],
                        artifact,
                    )
                body = _all_text(page).casefold()
                auth_location = _authentication_required(page)
                if auth_location or any(
                    value in body for value in ("войти", "авторизоваться", "авторизация")
                ):
                    result = _authorization_failure(
                        page,
                        url,
                        "страница показывает вход/авторизацию",
                    )
                    browser.close()
                    return result
                labels = _diagnostic_labels(page)
                detail = "кнопка участия не найдена после закрытия cookie"
                if preparations:
                    detail += "; подготовка: " + " | ".join(preparations)
                if labels:
                    detail += f"; видимые действия: {labels}"
                detail = _detail_with_page_hint(page, detail)
                artifact = _save_diagnostics(
                    page,
                    url,
                    "button_not_found",
                    detail,
                )
                browser.close()
                return auto.ParticipationResult(
                    False,
                    "button_not_found",
                    detail[:300],
                    artifact,
                )

            for _ in range(20):
                page.wait_for_timeout(500)
                confirmed, confirmation = _post_click_confirmed(page)
                if confirmed:
                    detail = (
                        f"BetBoom подтвердил участие после нажатия ({location}; {confirmation})"
                    )
                    if preparations:
                        detail += "; подготовка: " + " | ".join(preparations)
                    detail = _detail_with_page_hint(page, detail)
                    browser.close()
                    return auto.ParticipationResult(True, "participated", detail[:300])
                if confirmation.startswith("authentication_required:"):
                    result = _authorization_failure(
                        page,
                        url,
                        "страница показывает вход/авторизацию после нажатия участия",
                    )
                    browser.close()
                    return result
                referral_refusal = _visible_referral_ineligible(page)
                if referral_refusal:
                    detail = _detail_with_page_hint(
                        page,
                        f"referral_ineligible_exact_text:{referral_refusal}",
                    )
                    artifact = _save_diagnostics(
                        page, url, "referral_ineligible", detail
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        False,
                        "referral_ineligible",
                        detail[:300],
                        artifact,
                    )

            try:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1000)
                _prepare_page(page, timeout_ms)
            except Exception:
                pass
            for _ in range(10):
                confirmed, confirmation = _post_click_confirmed(page)
                if confirmed:
                    detail = _detail_with_page_hint(
                        page,
                        "BetBoom подтвердил участие после контрольной перезагрузки "
                        f"({location}; {confirmation})",
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        True,
                        "participated",
                        detail[:300],
                    )
                if confirmation.startswith("authentication_required:"):
                    result = _authorization_failure(
                        page,
                        url,
                        "контрольная перезагрузка показывает вход/авторизацию; участие не подтверждено",
                    )
                    browser.close()
                    return result
                page.wait_for_timeout(700)

            referral_refusal = _visible_referral_ineligible(page)
            if referral_refusal:
                detail = _detail_with_page_hint(
                    page,
                    f"referral_ineligible_exact_text:{referral_refusal}",
                )
                artifact = _save_diagnostics(
                    page, url, "referral_ineligible", detail
                )
                browser.close()
                return auto.ParticipationResult(
                    False,
                    "referral_ineligible",
                    detail[:300],
                    artifact,
                )

            detail = (
                f"participation control clicked ({location}), "
                "but no exact post-click confirmation was found"
            )
            labels = _diagnostic_labels(page)
            if labels:
                detail += f"; видимые действия после клика: {labels}"
            detail = _detail_with_page_hint(page, detail)
            artifact = _save_diagnostics(page, url, "unconfirmed", detail)
            browser.close()
            return auto.ParticipationResult(
                False,
                "unconfirmed",
                detail[:300],
                artifact,
            )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:300]
        artifact = _save_diagnostics(page, url, "technical_error", detail)
        return auto.ParticipationResult(
            False,
            "technical_error",
            detail,
            artifact,
        )


def self_test() -> None:
    assert _matches_full_label(CLICK_RE, "Участвовать")
    assert _matches_full_label(CLICK_RE, "  Принять   участие  ")
    assert not _matches_full_label(
        CLICK_RE,
        "В розыгрыше могут участвовать все зарегистрированные пользователи",
    )
    assert _matches_full_label(
        REFERRAL_INELIGIBLE_LABEL_RE,
        "Ваш аккаунт не является рефералом.",
    )
    assert not _matches_full_label(
        REFERRAL_INELIGIBLE_LABEL_RE,
        "Колесико для рефов",
    )
    assert _matches_full_label(SUCCESS_LABEL_RE, "Вы уже участвуете")
    assert _matches_full_label(SUCCESS_LABEL_RE, "Вы уже участвуете в розыгрыше!")
    assert not _matches_full_label(
        SUCCESS_LABEL_RE,
        "Если вы участвуете, дождитесь окончания таймера",
    )
    assert _matches_full_label(COOKIE_RE, "Окей")
    assert _matches_full_label(PROMO_DETAILS_RE, "Об акции")
    assert _matches_full_label(AUTH_RE, "Войти")
    assert not _matches_full_label(PROMO_DETAILS_RE, "Другие акции")
    assert PROMO_DETAILS_RE not in _preparation_patterns()
    assert _accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
        authentication_required=False,
    )
    assert not _accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
        authentication_required=True,
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