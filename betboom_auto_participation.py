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
_BUTTON_RE = re.compile(
    r"^\s*(?:участвую|участвовать|принять\s+участие)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParticipationResult:
    success: bool
    status: str
    detail: str


def enabled() -> bool:
    return os.getenv("BETBOOM_AUTO_PARTICIPATE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _storage_state_raw() -> str:
    direct = os.getenv("BETBOOM_STORAGE_STATE_JSON", "").strip()
    if direct:
        return direct

    # GitHub Actions repository secrets are size-limited. Large Playwright
    # storage-state JSON can therefore be stored in two secrets and joined
    # byte-for-byte at runtime. Do not strip either part: a split may occur
    # inside a JSON string where whitespace is significant.
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
            "не задан корректный BETBOOM_STORAGE_STATE_JSON или две части PART1/PART2",
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
                    "кнопка «Участвую»/«Участвовать»/«Принять участие» не найдена",
                )

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


def _event_token(key: str, entry: dict[str, Any]) -> str:
    """Return a stable identity for one concrete use of a wheel link."""

    normalized = str(key or "").casefold()
    event_id = str(entry.get("event_id") or entry.get("generation_id") or "").strip()
    if event_id:
        return f"{normalized}#event:{event_id}"

    try:
        action_id = int(entry.get("action_id") or 0)
    except (TypeError, ValueError):
        action_id = 0
    server_start = str(entry.get("server_start_at") or "").strip()
    if action_id > 0:
        return f"{normalized}#action:{action_id}:{server_start}"

    first_seen = str(
        entry.get("first_notified_at")
        or entry.get("message_date")
        or entry.get("created_at")
        or ""
    ).strip()
    return f"{normalized}#seen:{first_seen}"


def _eligible_for_event_attempt(entry: dict[str, Any], monitor: Any, current: Any) -> bool:
    url = str(entry.get("url") or "").strip()
    if not url:
        return False
    available_at = monitor.parse_datetime(entry.get("available_at"))
    if available_at is not None and available_at > current:
        return False
    if str(entry.get("verification_status") or "") == monitor.WHEEL_VERIFICATION_FAILED:
        return False
    if str(entry.get("page_status") or "").casefold() == "not_started":
        return False
    return True


def _mark_confirmed_participation(
    state: dict[str, Any],
    monitor: Any,
    normalized: str,
    entry: dict[str, Any],
    result: ParticipationResult,
    current: Any,
) -> None:
    context = {
        "wheel_key": normalized,
        "identifier": str(entry.get("identifier") or normalized),
        "url": str(entry.get("url") or ""),
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


def process_new_wheel_events(
    state: dict[str, Any], monitor: Any
) -> dict[str, int | bool]:
    """Attempt BetBoom participation once when a new wheel event becomes active.

    This method performs no periodic page polling. Each unique event identity is
    handled at most once. A wheel discovered before participation opens remains
    unrecorded until it becomes eligible, then receives its single attempt.
    """

    if not configured():
        return {"changed": False, "attempted": 0, "succeeded": 0, "failed": 0}

    current = monitor.now_utc()
    active = state.setdefault("active_wheels", {})
    events = state.setdefault("auto_participation_events", {})
    changed = False

    # On the first deployment in event mode, treat the currently active set as a
    # baseline. This prevents the new integration from opening a backlog of old
    # wheels. Only events appearing after this initialization are auto-processed.
    if not state.get("auto_participation_event_mode_initialized_at"):
        for key, entry in list(active.items()):
            if not isinstance(entry, dict):
                continue
            token = _event_token(str(key), entry)
            if not token or token in events:
                continue
            events[token] = {
                "wheel_key": str(key).casefold(),
                "status": "baseline_existing",
                "recorded_at": current.isoformat(),
            }
        state["auto_participation_event_mode_initialized_at"] = current.isoformat()
        return {"changed": True, "attempted": 0, "succeeded": 0, "failed": 0}

    attempted = 0
    succeeded = 0
    failed = 0

    for key, entry in list(active.items()):
        if not isinstance(entry, dict):
            continue
        normalized = str(key).casefold()
        token = _event_token(normalized, entry)
        if not token or token in events:
            continue

        if monitor.is_participating(state, normalized):
            events[token] = {
                "wheel_key": normalized,
                "status": "already_marked_in_bot",
                "recorded_at": current.isoformat(),
            }
            changed = True
            continue

        if not _eligible_for_event_attempt(entry, monitor, current):
            continue

        attempted += 1
        result = participate(str(entry.get("url") or ""))
        events[token] = {
            "wheel_key": normalized,
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

        _mark_confirmed_participation(state, monitor, normalized, entry, result, current)
        succeeded += 1

    return {
        "changed": changed,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
    }


def process_active_wheels(state: dict[str, Any], monitor: Any) -> dict[str, int | bool]:
    """Manual fallback: retry active wheels when this worker is explicitly run."""

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
        if not _eligible_for_event_attempt(entry, monitor, current):
            continue

        previous = attempts.get(normalized)
        if isinstance(previous, dict):
            previous_at = monitor.parse_datetime(previous.get("attempted_at"))
            if previous_at is not None and current - previous_at < retry_delta:
                continue

        attempted += 1
        result = participate(str(entry.get("url") or ""))
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

        _mark_confirmed_participation(state, monitor, normalized, entry, result, current)
        succeeded += 1

    return {
        "changed": changed,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
    }
