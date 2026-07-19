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
        "1",
        "true",
        "yes",
        "on",
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
    """Open one BetBoom wheel and make exactly one participation attempt."""

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


def _normalized_names(user_id: str, record: dict[str, Any]) -> set[str]:
    first = str(record.get("first_name") or "").strip()
    last = str(record.get("last_name") or "").strip()
    full = " ".join(value for value in (first, last) if value)
    values = {
        first,
        full,
        str(record.get("name") or "").strip(),
        str(record.get("display_name") or "").strip(),
        str(record.get("username") or "").strip().lstrip("@"),
        str(user_id or "").strip(),
    }
    return {value.casefold() for value in values if value}


def _target_chat_id() -> tuple[str, str]:
    target = os.getenv("BETBOOM_PARTICIPATION_ALERT_USER", _DEFAULT_ALERT_USER).strip()
    normalized_target = target.casefold()
    try:
        import bot_notification_state

        config, exists = bot_notification_state.load_config()
    except Exception as exc:
        return "", f"config_error:{type(exc).__name__}"
    if not exists:
        return "", "config_missing"

    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    for user_id, raw in users.items():
        if not isinstance(raw, dict):
            continue
        names = _normalized_names(str(user_id), raw)
        exact = normalized_target in names
        first_name_match = any(
            value == normalized_target or value.startswith(normalized_target + " ")
            for value in names
        )
        if exact or first_name_match:
            chat_id = str(raw.get("chat_id") or user_id).strip()
            if chat_id:
                return chat_id, str(user_id)
    return "", "recipient_not_found"


def _notify_manual_participation(
    monitor: Any,
    entry: dict[str, Any],
    result: ParticipationResult,
) -> tuple[bool, str]:
    chat_id, recipient = _target_chat_id()
    if not chat_id:
        return False, recipient

    identifier = str(entry.get("identifier") or entry.get("wheel_key") or "колесо")
    url = str(entry.get("url") or "").strip()
    text = (
        "⚠️ <b>Автоучастие в колесе BetBoom не сработало</b>\n\n"
        f"Вячеслав, не удалось автоматически принять участие в колесе <code>{identifier}</code>.\n"
        "Повторной автоматической попытки не будет. Пожалуйста, откройте колесо и нажмите «Участвовать» вручную."
    )
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "🎡 Открыть колесо", "url": url}]]
        }

    try:
        response = monitor.telegram_api("sendMessage", payload)
    except Exception as exc:
        return False, f"send_error:{type(exc).__name__}:{exc}"[:300]
    if isinstance(response, dict) and response.get("ok"):
        return True, recipient
    return False, f"telegram_rejected:{str(response)[:220]}"


def process_new_wheel_events(
    state: dict[str, Any], monitor: Any
) -> dict[str, int | bool]:
    """Attempt each new wheel event exactly once; failures require manual action."""

    if not configured():
        return {"changed": False, "attempted": 0, "succeeded": 0, "failed": 0}

    current = monitor.now_utc()
    active = state.setdefault("active_wheels", {})
    events = state.setdefault("auto_participation_events", {})
    changed = False

    # First event-mode deployment establishes a baseline so historical active
    # wheels are never opened by the participation browser.
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

        # Record the event immediately. This is the hard no-retry boundary:
        # success or failure, the same event token is never attempted again.
        event_record: dict[str, Any] = {
            "wheel_key": normalized,
            "attempted_at": current.isoformat(),
            "status": result.status,
            "detail": result.detail[:300],
            "retry_allowed": False,
        }
        events[token] = event_record
        entry["auto_participation_status"] = result.status
        entry["auto_participation_checked_at"] = current.isoformat()
        entry["auto_participation_retry_allowed"] = False
        changed = True

        if not result.success:
            failed += 1
            entry["auto_participation_error"] = result.detail[:300]
            notified, notification_detail = _notify_manual_participation(
                monitor, entry, result
            )
            event_record["manual_notification_sent"] = notified
            event_record["manual_notification_detail"] = notification_detail[:300]
            if notified:
                event_record["manual_notification_at"] = current.isoformat()
                entry["auto_participation_manual_notification_at"] = current.isoformat()
            else:
                entry["auto_participation_manual_notification_error"] = (
                    notification_detail[:300]
                )
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
    """Compatibility entry point; intentionally uses the same one-attempt policy."""

    return process_new_wheel_events(state, monitor)
