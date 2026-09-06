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
import betboom_network_diagnostics as network_diag

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
# This is the real self-contained BetBoom wheel status seen in production.  It
# deliberately matches the whole status element, not an arbitrary ancestor or
# promo card containing the same words.
ACCOUNT_STATUS_RE = re.compile(
    r"(?:отлично!\s*)?"
    r"теперь\s+ты\s+участвуешь\s+в\s+розыгрыше[.!]?"
    r"(?:\s+(?:"
    r"жди\s+завершения\s+таймера,?\s*чтобы\s+забрать\s+приз|"
    r"скоро\s+узнаешь\s+результат"
    r")[.!]?)?",
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
PRECLICK_EXACT_CONFIRMATION_MARKER = "preclick_exact_success_label"


def _normalized_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _matches_full_label(pattern: re.Pattern[str], value: object) -> bool:
    return bool(pattern.fullmatch(_normalized_label(value)))


def _matches_success_label(
    value: object,
    *,
    allow_embedded: bool = False,
) -> bool:
    label = _normalized_label(value)
    if _matches_full_label(SUCCESS_LABEL_RE, label):
        return True
    if _matches_full_label(ACCOUNT_STATUS_RE, label):
        return True
    if not allow_embedded:
        return False
    return bool(
        label
        and len(label) <= 500
        and EMBEDDED_SUCCESS_LABEL_RE.search(label)
    )


def _search_roots(page: Any) -> list[Any]:
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
    return _visible_control_location(page, AUTH_RE)


def _visible_referral_ineligible(page: Any) -> str:
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


def _success_location(page: Any, *, allow_embedded: bool = False) -> str:
    """Return one visible BetBoom account-state confirmation.

    Before a click, a candidate must be a self-contained success/status element.
    This accepts the real BetBoom paragraph beginning with «Отлично!» while still
    rejecting a larger promo card/ancestor that merely contains those words.
    Embedded text remains a post-click-only fallback because our own click then
    provides the causal evidence.
    """

    if _authentication_required(page):
        return ""
    for root in _search_roots(page):
        try:
            locators = (
                root.locator('p,[role="status"],[aria-live]').filter(
                    has_text=SUCCESS_LABEL_RE
                ),
                root.locator('p,[role="status"],[aria-live]').filter(
                    has_text=ACCOUNT_STATUS_RE
                ),
                root.get_by_text(SUCCESS_LABEL_RE),
                root.get_by_text(ACCOUNT_STATUS_RE),
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
                    label = candidate.inner_text(timeout=1000)
                    if _matches_success_label(label, allow_embedded=allow_embedded):
                        normalized = _normalized_label(label)
                        return f"{_root_name(root, page)}:{normalized}"[:260]
                except Exception:
                    continue
    return ""


def _success(page: Any, *, allow_embedded: bool = False) -> bool:
    return bool(_success_location(page, allow_embedded=allow_embedded))


def _artifact_root() -> Path:
    return Path(
        os.getenv(
            "BBVG_BROWSER_ARTIFACT_DIR",
            str(Path(__file__).resolve().parent / "runtime" / "browser_diagnostics"),
        )
    )


def _new_artifact_target(url: str, status: str) -> Path | None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(
        f"{url}\x1f{status}\x1f{stamp}".encode("utf-8")
    ).hexdigest()[:12]
    target = _artifact_root() / f"{stamp}-{digest}"
    try:
        target.mkdir(parents=True, exist_ok=False)
    except Exception:
        return None
    return target


def _capture_page(page: Any, target: Path, name: str) -> None:
    page.screenshot(path=str(target / f"{name}.png"), full_page=True)
    (target / f"{name}.html").write_text(
        str(page.content() or ""),
        encoding="utf-8",
    )


def _write_metadata(target: Path, payload: dict[str, Any]) -> None:
    (target / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _diagnostic_labels(page: Any) -> str:
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
    return " | ".join(labels)[:260]


def _save_diagnostics(page: Any, url: str, status: str, detail: str) -> str:
    if page is None:
        return ""
    target = _new_artifact_target(url, status)
    if target is None:
        return ""
    try:
        _capture_page(page, target, "page")
        _write_metadata(
            target,
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
        )
    except Exception:
        return ""
    return str(target)


def _save_preexisting_proof(page: Any, url: str, confirmation: str) -> str:
    target = _new_artifact_target(url, "already_participating")
    if target is None:
        return ""
    try:
        _capture_page(page, target, "preexisting")
        _write_metadata(
            target,
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "url": url,
                "final_url": str(getattr(page, "url", "") or ""),
                "status": "already_participating",
                "phase": "pre_click",
                "clicked_by_bot": False,
                "participation_control_seen": False,
                "confirmation": confirmation[:500],
                "visible_controls": _diagnostic_labels(page),
            },
        )
    except Exception:
        return ""
    return str(target)


def _start_click_proof(page: Any, url: str, click_location: str) -> Path | None:
    target = _new_artifact_target(url, "participation_trace")
    if target is None:
        return None
    try:
        _capture_page(page, target, "before_click")
        _write_metadata(
            target,
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "url": url,
                "final_url": str(getattr(page, "url", "") or ""),
                "status": "click_pending_confirmation",
                "phase": "before_click",
                "clicked_by_bot": False,
                "participation_control_seen": True,
                "participation_control": click_location[:300],
            },
        )
    except Exception:
        return None
    return target


def _finish_click_proof(
    page: Any,
    target: Path | None,
    *,
    url: str,
    click_location: str,
    confirmation: str,
    status: str,
) -> str:
    if target is None:
        return _save_diagnostics(
            page,
            url,
            status,
            f"clicked_by_bot=true; click={click_location}; confirmation={confirmation}",
        )
    try:
        _capture_page(page, target, "after_click")
        _write_metadata(
            target,
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "url": url,
                "final_url": str(getattr(page, "url", "") or ""),
                "status": status,
                "phase": "post_click",
                "clicked_by_bot": True,
                "participation_control_seen": True,
                "participation_control": click_location[:300],
                "confirmation": confirmation[:500],
                "visible_controls": _diagnostic_labels(page),
            },
        )
    except Exception:
        return str(target)
    return str(target)


def _authorization_failure(page: Any, url: str, detail: str) -> auto.ParticipationResult:
    artifact = _save_diagnostics(page, url, "authorization_required", detail)
    return auto.ParticipationResult(
        False,
        "authorization_required",
        detail[:300],
        artifact,
    )


def _click_in_root(root: Any, timeout_ms: int) -> tuple[bool, str]:
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
    return (COOKIE_RE,)


def _prepare_page(page: Any, timeout_ms: int) -> list[str]:
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
    return (
        promo_details_visible
        and not participation_visible
        and not authentication_required
    )


def _post_click_confirmed(page: Any) -> tuple[bool, str]:
    auth_location = _authentication_required(page)
    if auth_location:
        return False, f"authentication_required:{auth_location}"[:180]
    exact = _success_location(page)
    if exact:
        return True, f"exact_success_label:{exact}"[:260]
    embedded = _success_location(page, allow_embedded=True)
    if embedded:
        return True, f"embedded_success_label:{embedded}"[:260]
    participation_location = _visible_control_location(page, CLICK_RE)
    promo_location = _visible_control_location(page, PROMO_DETAILS_RE)
    if _accepted_post_click_layout(
        participation_visible=bool(participation_location),
        promo_details_visible=bool(promo_location),
        authentication_required=False,
    ):
        return True, f"post_click_layout:{promo_location}"[:180]
    return False, ""


def _find_and_click(
    page: Any,
    timeout_ms: int,
) -> tuple[bool, str, list[str], str]:
    preparations: list[str] = []
    for _ in range(8):
        success_location = _success_location(page)
        if success_location:
            return False, "already_participating", preparations, success_location
        for item in _prepare_page(page, timeout_ms):
            if item not in preparations:
                preparations.append(item)
        success_location = _success_location(page)
        if success_location:
            return False, "already_participating", preparations, success_location
        clicked, location = _click_candidates(page, timeout_ms)
        if clicked:
            return True, location, preparations, ""
        try:
            page.wait_for_timeout(500)
        except Exception:
            break
    return False, "", preparations, ""


def _resolved_storage_state(
    storage_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if isinstance(storage_state, dict):
        return storage_state
    return auto._storage_state()


def participate(
    url: str,
    storage_state: dict[str, Any] | None = None,
) -> auto.ParticipationResult:
    """Open one wheel using an explicit session when supplied.

    The optional argument keeps the primary-account API backward compatible,
    while secondary account runners can inject their own storage state directly
    instead of mutating global session lookup functions.
    """

    if not url.startswith("https://betboom.ru/freestream/"):
        return auto.ParticipationResult(
            False,
            "technical_error",
            "invalid_url: некорректная ссылка BetBoom",
        )

    resolved_storage_state = _resolved_storage_state(storage_state)
    if resolved_storage_state is None:
        return auto.ParticipationResult(
            False,
            "technical_error",
            "not_configured: сессия BetBoom не настроена",
        )

    page: Any = None
    network_trace: list[dict[str, Any]] = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return auto.ParticipationResult(
            False,
            "technical_error",
            "dependency_missing: Playwright не установлен",
        )

    timeout_ms = max(
        8000,
        min(60000, int(os.getenv("BETBOOM_PARTICIPATION_TIMEOUT_MS", "30000"))),
    )
    channel = os.getenv("BETBOOM_BROWSER_CHANNEL", "chrome").strip() or "chrome"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel=channel)
            context = browser.new_context(storage_state=resolved_storage_state)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(800)

            referral_refusal = _visible_referral_ineligible(page)
            if referral_refusal:
                detail = f"referral_ineligible_exact_text:{referral_refusal}"
                artifact = _save_diagnostics(page, url, "referral_ineligible", detail)
                browser.close()
                return auto.ParticipationResult(
                    False, "referral_ineligible", detail[:300], artifact
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

            network_trace = network_diag.attach(page)
            clicked, location, preparations, preexisting = _find_and_click(
                page, timeout_ms
            )
            if location == "already_participating":
                artifact = _save_preexisting_proof(page, url, preexisting)
                detail = (
                    "BetBoom уже показывал участие до клика; "
                    "clicked_by_bot=false; "
                    f"confirmation={PRECLICK_EXACT_CONFIRMATION_MARKER}"
                )
                browser.close()
                return auto.ParticipationResult(
                    True, "already_participating", detail[:300], artifact
                )

            if not clicked:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                clicked, location, retried_preparations, preexisting = _find_and_click(
                    page, timeout_ms
                )
                for item in retried_preparations:
                    if item not in preparations:
                        preparations.append(item)
                if location == "already_participating":
                    artifact = _save_preexisting_proof(page, url, preexisting)
                    detail = (
                        "BetBoom уже показывал участие до клика после перезагрузки; "
                        "clicked_by_bot=false; "
                        f"confirmation={PRECLICK_EXACT_CONFIRMATION_MARKER}"
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        True, "already_participating", detail[:300], artifact
                    )

            if not clicked:
                referral_refusal = _visible_referral_ineligible(page)
                if referral_refusal:
                    detail = f"referral_ineligible_exact_text:{referral_refusal}"
                    artifact = _save_diagnostics(
                        page, url, "referral_ineligible", detail
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        False, "referral_ineligible", detail[:300], artifact
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
                labels = _diagnostic_labels(page)
                detail = "кнопка участия не найдена после закрытия cookie"
                if preparations:
                    detail += "; подготовка: " + " | ".join(preparations)
                if labels:
                    detail += f"; видимые действия: {labels}"
                artifact = _save_diagnostics(page, url, "button_not_found", detail)
                browser.close()
                return auto.ParticipationResult(
                    False, "button_not_found", detail[:300], artifact
                )

            # The click happened inside _find_and_click. Capture the immediate
            # post-click page and retain the exact clicked control in metadata.
            proof_target = _new_artifact_target(url, "participation_trace")
            if proof_target is not None:
                try:
                    _capture_page(page, proof_target, "immediately_after_click")
                    _write_metadata(
                        proof_target,
                        {
                            "captured_at": datetime.now(UTC).isoformat(),
                            "url": url,
                            "final_url": str(getattr(page, "url", "") or ""),
                            "status": "click_sent",
                            "phase": "post_click_pending_confirmation",
                            "clicked_by_bot": True,
                            "participation_control_seen": True,
                            "participation_control": location[:300],
                        },
                    )
                except Exception:
                    proof_target = None

            for _ in range(20):
                page.wait_for_timeout(500)
                confirmed, confirmation = _post_click_confirmed(page)
                if confirmed:
                    detail = (
                        "BetBoom подтвердил участие после клика бота; "
                        f"clicked_by_bot=true; click={location}; confirmation={confirmation}"
                    )
                    if preparations:
                        detail += "; подготовка: " + " | ".join(preparations)
                    artifact = _finish_click_proof(
                        page,
                        proof_target,
                        url=url,
                        click_location=location,
                        confirmation=confirmation,
                        status="participated",
                    )
                    network_diag.write_trace(artifact, network_trace)
                    browser.close()
                    return auto.ParticipationResult(
                        True, "participated", detail[:300], artifact
                    )
                if confirmation.startswith("authentication_required:"):
                    result = _authorization_failure(
                        page,
                        url,
                        "страница показывает вход/авторизацию после клика участия",
                    )
                    network_diag.write_trace(result.artifact_url, network_trace)
                    browser.close()
                    return result
                referral_refusal = _visible_referral_ineligible(page)
                if referral_refusal:
                    detail = f"referral_ineligible_exact_text:{referral_refusal}"
                    artifact = _save_diagnostics(
                        page, url, "referral_ineligible", detail
                    )
                    network_diag.write_trace(artifact, network_trace)
                    browser.close()
                    return auto.ParticipationResult(
                        False, "referral_ineligible", detail[:300], artifact
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
                    detail = (
                        "BetBoom подтвердил участие после контрольной перезагрузки; "
                        f"clicked_by_bot=true; click={location}; confirmation={confirmation}"
                    )
                    artifact = _finish_click_proof(
                        page,
                        proof_target,
                        url=url,
                        click_location=location,
                        confirmation=confirmation,
                        status="participated",
                    )
                    network_diag.write_trace(artifact, network_trace)
                    browser.close()
                    return auto.ParticipationResult(
                        True, "participated", detail[:300], artifact
                    )
                if confirmation.startswith("authentication_required:"):
                    result = _authorization_failure(
                        page,
                        url,
                        "контрольная перезагрузка показывает вход/авторизацию; участие не подтверждено",
                    )
                    network_diag.write_trace(result.artifact_url, network_trace)
                    browser.close()
                    return result
                page.wait_for_timeout(700)

            detail = (
                "кнопка участия нажата ботом, но подтверждение BetBoom не найдено; "
                f"clicked_by_bot=true; click={location}"
            )
            artifact = _finish_click_proof(
                page,
                proof_target,
                url=url,
                click_location=location,
                confirmation="not_found",
                status="unconfirmed",
            )
            network_diag.write_trace(artifact, network_trace)
            browser.close()
            return auto.ParticipationResult(
                False, "unconfirmed", detail[:300], artifact
            )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:300]
        artifact = _save_diagnostics(page, url, "technical_error", detail)
        network_diag.write_trace(artifact, network_trace)
        return auto.ParticipationResult(
            False, "technical_error", detail, artifact
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
    assert _matches_full_label(SUCCESS_LABEL_RE, "Вы уже участвуете")
    real_status = (
        "Отлично! Теперь ты участвуешь в розыгрыше. "
        "Жди завершения таймера, чтобы забрать приз"
    )
    assert _matches_success_label(real_status)
    promo_ancestor = "ПОДГОН ОТ DEKO\n" + real_status
    assert not _matches_success_label(promo_ancestor)
    assert _matches_success_label(promo_ancestor, allow_embedded=True)
    assert _matches_full_label(COOKIE_RE, "Окей")
    assert _matches_full_label(PROMO_DETAILS_RE, "Об акции")
    assert _matches_full_label(AUTH_RE, "Войти")
    assert PROMO_DETAILS_RE not in _preparation_patterns()
    network_diag.self_test()
    injected = {"cookies": [{"name": "session", "value": "injected"}]}
    original_storage = auto._storage_state
    auto._storage_state = lambda: (_ for _ in ()).throw(
        AssertionError("global storage lookup must not run for injected state")
    )
    try:
        assert _resolved_storage_state(injected) is injected
    finally:
        auto._storage_state = original_storage
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
    print("BetBoom participation proof self-test passed")


if __name__ == "__main__":
    self_test()