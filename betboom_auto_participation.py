from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from bbvg.storage import event_id_from_entry, legacy_event_aliases
import wheel_publications_v2


_SUCCESS_RE = re.compile(
    r"(?:участие\s+(?:принято|подтверждено|зарегистрировано)|"
    r"вы\s+(?:уже\s+)?участвуете|уже\s+участвуете|участие\s+отмечено|"
    r"теперь\s+ты\s+участвуешь\s+в\s+розыгрыше|вы\s+в\s+розыгрыше)",
    re.IGNORECASE,
)
_BUTTON_RE = re.compile(
    r"^\s*(?:участвую|участвовать|принять\s+участие)\s*$",
    re.IGNORECASE,
)
_DEFAULT_ALERT_USER = "Вячеслав"
_PARTICIPATION_ATTEMPT_VERSION = 2
PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"
PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"
PRIMARY_ACCOUNT_OWNER = "vyacheslav"
PRIMARY_ACCOUNT_ORDER = 10
SECONDARY_ACCOUNT_KEY = "vyacheslav_secondary"
SECONDARY_ACCOUNT_LABEL = "Аккаунт 2"
SECONDARY_ACCOUNT_ORDER = 20
SUCCESS_STATUSES = {
    "participated",
    "already_participating",
    "already_marked_participating",
}


@dataclass(frozen=True)
class ParticipationResult:
    success: bool
    status: str
    detail: str
    artifact_url: str = ""


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


def _page_state_hint(page: Any, body_text: str) -> str:
    lowered = str(body_text or "").casefold()
    current_url = str(getattr(page, "url", "") or "")
    if any(marker in lowered for marker in ("войти", "авторизоваться", "авторизация")):
        return "страница BetBoom показывает вход/авторизацию"
    if _BUTTON_RE.search(body_text or ""):
        return "текст участия появился, но кликабельный элемент не распознан"
    if "колес" in lowered or "/freestream/" in current_url.casefold():
        return "страница колеса загрузилась, но кнопка участия не появилась"
    if current_url:
        return f"интерфейс участия не загрузился; текущий URL: {current_url[:180]}"
    return "интерфейс участия не загрузился"


def _visible_participation_control(page: Any) -> Any | None:
    candidates = (
        page.get_by_role("button", name=_BUTTON_RE),
        page.locator("button").filter(has_text=_BUTTON_RE),
        page.locator('[role="button"]').filter(has_text=_BUTTON_RE),
        page.get_by_text(_BUTTON_RE, exact=True),
    )
    for locator in candidates:
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def _wait_for_participation_control(page: Any, timeout_ms: int) -> Any | None:
    """Wait for SPA hydration, then resolve a semantic or text participation control."""

    wait_ms = max(2500, min(timeout_ms, 12000))
    try:
        page.get_by_text(_BUTTON_RE, exact=True).first.wait_for(
            state="visible",
            timeout=wait_ms,
        )
    except Exception:
        pass
    return _visible_participation_control(page)


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

            last_body = _body_text(page)
            if _SUCCESS_RE.search(last_body):
                browser.close()
                return ParticipationResult(
                    True,
                    "already_participating",
                    "BetBoom уже показывает подтверждённое участие",
                )

            control = _wait_for_participation_control(page, timeout_ms)
            if control is None:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:
                    pass
                last_body = _body_text(page)
                if _SUCCESS_RE.search(last_body):
                    browser.close()
                    return ParticipationResult(
                        True,
                        "already_participating",
                        "BetBoom уже показывает подтверждённое участие после повторной загрузки",
                    )
                control = _wait_for_participation_control(page, timeout_ms)

            if control is None:
                hint = _page_state_hint(page, _body_text(page) or last_body)
                browser.close()
                return ParticipationResult(
                    False,
                    "button_not_found",
                    f"кнопка участия не найдена после ожидания и повторной загрузки; {hint}"[:300],
                )

            control.click(timeout=timeout_ms)
            try:
                page.wait_for_function(
                    r"""() => /участие\s+(принято|подтверждено|зарегистрировано)|вы\s+(уже\s+)?участвуете|уже\s+участвуете|участие\s+отмечено|теперь\s+ты\s+участвуешь\s+в\s+розыгрыше|вы\s+в\s+розыгрыше/i.test(document.body?.innerText || '')""",
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


def register_account(
    state: dict[str, Any],
    *,
    account_key: str,
    account_label: str,
    account_owner: str,
    account_order: int,
) -> bool:
    registry = state.setdefault("auto_participation_account_registry", {})
    record = {
        "account_key": str(account_key),
        "account_label": str(account_label or account_key),
        "account_owner": str(account_owner),
        "account_order": int(account_order),
        "enabled": True,
    }
    if registry.get(account_key) == record:
        return False
    registry[account_key] = record
    return True


def ensure_default_account_registry(state: dict[str, Any]) -> bool:
    changed = register_account(
        state,
        account_key=PRIMARY_ACCOUNT_KEY,
        account_label=PRIMARY_ACCOUNT_LABEL,
        account_owner=PRIMARY_ACCOUNT_OWNER,
        account_order=PRIMARY_ACCOUNT_ORDER,
    )
    changed = register_account(
        state,
        account_key=SECONDARY_ACCOUNT_KEY,
        account_label=SECONDARY_ACCOUNT_LABEL,
        account_owner=PRIMARY_ACCOUNT_OWNER,
        account_order=SECONDARY_ACCOUNT_ORDER,
    ) or changed
    return changed


def _event_token(key: str, entry: dict[str, Any]) -> str:
    """Return the canonical generation identity shared by every component."""

    return event_id_from_entry(entry, wheel_key=key)


def _record_account_key(token: str, record: dict[str, Any]) -> str:
    explicit = str(record.get("account_key") or "").strip()
    if explicit:
        return explicit
    if "#account:" in str(token):
        return str(token).split("#account:", 1)[1].strip()
    return PRIMARY_ACCOUNT_KEY


def _record_success(record: Any) -> bool:
    return isinstance(record, dict) and str(record.get("status") or "").casefold() in SUCCESS_STATUSES


def _record_time(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    return max(
        (
            str(record.get(field) or "")
            for field in (
                "bot_success_pending_at",
                "bot_failure_pending_at",
                "attempted_at",
                "recorded_at",
            )
        ),
        default="",
    )


def merge_event_record(previous: Any, incoming: Any) -> dict[str, Any]:
    left = dict(previous) if isinstance(previous, dict) else {}
    right = dict(incoming) if isinstance(incoming, dict) else {}
    left_success = _record_success(left)
    right_success = _record_success(right)
    if left_success != right_success:
        winner, older = (left, right) if left_success else (right, left)
    elif _record_time(right) >= _record_time(left):
        winner, older = right, left
    else:
        winner, older = left, right
    result = dict(older)
    result.update(winner)
    if _record_success(result):
        for field in (
            "bot_failure_pending_at",
            "bot_failure_sync_status",
            "bot_failure_sync_version",
            "bot_failure_status",
            "bot_failure_detail",
            "manual_notification_sent",
            "manual_notification_at",
            "manual_notification_detail",
        ):
            result.pop(field, None)
        result["retry_allowed"] = False
    return result


def _record_matches_active_event(
    token: str,
    record: dict[str, Any],
    key: str,
    entry: dict[str, Any],
) -> bool:
    canonical = _event_token(key, entry)
    base = str(token).split("#account:", 1)[0]
    explicit = str(record.get("event_token") or "").split("#account:", 1)[0]
    identities = {canonical, *legacy_event_aliases(entry, wheel_key=key)}
    if identities.intersection({base, explicit}):
        return True
    legacy_id = str(entry.get("event_id") or entry.get("generation_id") or "").strip()
    if legacy_id and f"{key}#event:{legacy_id}" in {base, explicit}:
        return True
    context = record.get("event_context")
    if isinstance(context, dict) and _event_token(key, context) == canonical:
        return True
    return False


def canonicalize_primary_event_aliases(state: dict[str, Any]) -> bool:
    """Collapse legacy #event primary rows into the canonical #action row."""

    changed = ensure_default_account_registry(state)
    events = state.setdefault("auto_participation_events", {})
    active = state.get("active_wheels")
    if not isinstance(events, dict) or not isinstance(active, dict):
        return changed
    for raw_key, entry in list(active.items()):
        if not isinstance(entry, dict):
            continue
        key = str(raw_key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
        canonical = _event_token(key, entry)
        for raw_token in list(events):
            record = events.get(raw_token)
            if not isinstance(record, dict):
                continue
            if _record_account_key(str(raw_token), record) != PRIMARY_ACCOUNT_KEY:
                continue
            if not _record_matches_active_event(str(raw_token), record, key, entry):
                continue
            suffix = (
                f"#account:{PRIMARY_ACCOUNT_KEY}"
                if "#account:" in str(raw_token)
                else ""
            )
            target = canonical + suffix
            normalized = dict(record)
            normalized.update(
                {
                    "event_token": canonical,
                    "account_key": PRIMARY_ACCOUNT_KEY,
                    "account_label": PRIMARY_ACCOUNT_LABEL,
                    "account_owner": PRIMARY_ACCOUNT_OWNER,
                    "account_order": PRIMARY_ACCOUNT_ORDER,
                }
            )
            merged = merge_event_record(events.get(target), normalized)
            if events.get(target) != merged:
                events[target] = merged
                changed = True
            if str(raw_token) != target:
                events.pop(raw_token, None)
                changed = True
    return changed


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
        participant["participation_source"] = (
            "betboom_preexisting"
            if result.status == "already_participating"
            else "betboom_browser"
        )
        participant["participation_status"] = result.status
        participant["confirmed_at"] = current.isoformat()
    entry.pop("auto_participation_error", None)
    entry["auto_participation_confirmed_at"] = current.isoformat()
    entry["auto_participation_origin"] = (
        "preexisting_verified"
        if result.status == "already_participating"
        else "automatic"
    )


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


def _attempt_version(record: Any) -> int:
    if not isinstance(record, dict):
        return 0
    try:
        return int(record.get("attempt_version", 1) or 1)
    except (TypeError, ValueError):
        return 1


def _rearm_legacy_button_not_found(
    events: dict[str, Any],
    token: str,
    entry: dict[str, Any],
    monitor: Any,
    current: Any,
) -> tuple[bool, bool, str]:
    """Retry one old button_not_found event once under the improved SPA-aware finder."""

    previous = events.get(token)
    if not isinstance(previous, dict):
        return False, False, ""
    if str(previous.get("status") or "") != "button_not_found":
        return False, False, ""
    if _attempt_version(previous) >= _PARTICIPATION_ATTEMPT_VERSION:
        return False, False, ""
    if not _eligible_for_event_attempt(entry, monitor, current):
        return False, False, ""

    previous_notified = bool(
        previous.get("manual_notification_sent")
        or entry.get("auto_participation_manual_notification_at")
    )
    previous_notification_at = str(
        previous.get("manual_notification_at")
        or entry.get("auto_participation_manual_notification_at")
        or ""
    )
    events.pop(token, None)
    for field in (
        "auto_participation_status",
        "auto_participation_checked_at",
        "auto_participation_retry_allowed",
        "auto_participation_error",
        "auto_participation_manual_notification_error",
    ):
        entry.pop(field, None)
    entry["auto_participation_rearmed_at"] = current.isoformat()
    return True, previous_notified, previous_notification_at


def _rearm_unverified_bot_mark(
    events: dict[str, Any],
    token: str,
    entry: dict[str, Any],
    monitor: Any,
    current: Any,
) -> bool:
    """Migrate a Telegram-only mark back to a real BetBoom check.

    The bot button records user intent and rating input. It is not evidence that
    the account participated on BetBoom. Older workers incorrectly treated
    ``already_marked_in_bot`` as a successful account result and skipped the
    browser, so eligible legacy records must be checked again.
    """

    previous = events.get(token)
    if not isinstance(previous, dict):
        return False
    if str(previous.get("status") or "") != "already_marked_in_bot":
        return False
    if not _eligible_for_event_attempt(entry, monitor, current):
        return False
    events.pop(token, None)
    for field in (
        "auto_participation_status",
        "auto_participation_checked_at",
        "auto_participation_retry_allowed",
        "auto_participation_error",
    ):
        entry.pop(field, None)
    entry["auto_participation_rearmed_at"] = current.isoformat()
    entry["auto_participation_rearm_reason"] = (
        "bot_mark_requires_betboom_verification"
    )
    return True


def process_new_wheel_events(
    state: dict[str, Any], monitor: Any
) -> dict[str, int | bool]:
    """Attempt each new wheel event once per browser-attempt version."""

    if not configured():
        return {"changed": False, "attempted": 0, "succeeded": 0, "failed": 0}

    current = monitor.now_utc()
    active = state.setdefault("active_wheels", {})
    events = state.setdefault("auto_participation_events", {})
    changed = canonicalize_primary_event_aliases(state)

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
                "event_token": token,
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "account_owner": PRIMARY_ACCOUNT_OWNER,
                "account_order": PRIMARY_ACCOUNT_ORDER,
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
        if not token:
            continue

        previous_notification_sent = False
        previous_notification_at = ""
        if token in events:
            bot_mark_rearmed = _rearm_unverified_bot_mark(
                events,
                token,
                entry,
                monitor,
                current,
            )
            if bot_mark_rearmed:
                rearmed = True
                changed = True
                print(
                    "Rearmed unverified Telegram participation mark for "
                    f"BetBoom verification: wheel={normalized} token={token}"
                )
            else:
                rearmed, previous_notification_sent, previous_notification_at = (
                    _rearm_legacy_button_not_found(
                        events,
                        token,
                        entry,
                        monitor,
                        current,
                    )
                )
            if rearmed:
                changed = True
                if not bot_mark_rearmed:
                    print(
                        "Rearmed button_not_found event for SPA-aware "
                        f"participation retry: wheel={normalized} token={token}"
                    )
            else:
                continue

        if not _eligible_for_event_attempt(entry, monitor, current):
            continue

        attempted += 1
        result = participate(str(entry.get("url") or ""))
        wheel_publications_v2.apply_referral_context(
            state,
            entry,
            observed_at=current,
            browser_detail=result.detail,
        )

        event_record: dict[str, Any] = {
            "wheel_key": normalized,
            "event_token": token,
            "account_key": PRIMARY_ACCOUNT_KEY,
            "account_label": PRIMARY_ACCOUNT_LABEL,
            "account_owner": PRIMARY_ACCOUNT_OWNER,
            "account_order": PRIMARY_ACCOUNT_ORDER,
            "attempted_at": current.isoformat(),
            "status": result.status,
            "detail": result.detail[:300],
            "retry_allowed": False,
            "attempt_version": _PARTICIPATION_ATTEMPT_VERSION,
            "artifact_url": result.artifact_url,
            "confirmation_method": (
                "betboom_preexisting"
                if result.status == "already_participating"
                else "betboom_post_click"
                if result.status == "participated"
                else "betboom_unconfirmed"
            ),
            "participation_origin": (
                "preexisting_verified"
                if result.status == "already_participating"
                else "automatic"
                if result.status == "participated"
                else "unverified"
            ),
        }
        event_record = merge_event_record(events.get(token), event_record)
        events[token] = event_record
        entry["auto_participation_status"] = event_record["status"]
        entry["auto_participation_checked_at"] = current.isoformat()
        entry["auto_participation_retry_allowed"] = False
        changed = True

        if _record_success(event_record):
            result = ParticipationResult(True, str(event_record.get("status") or "participated"), str(event_record.get("detail") or result.detail))

        if not result.success:
            failed += 1
            entry["auto_participation_error"] = result.detail[:300]
            if previous_notification_sent:
                event_record["manual_notification_sent"] = True
                event_record["manual_notification_detail"] = "previously_sent"
                if previous_notification_at:
                    event_record["manual_notification_at"] = previous_notification_at
            else:
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
    """Compatibility entry point; intentionally uses the same event policy."""

    return process_new_wheel_events(state, monitor)
