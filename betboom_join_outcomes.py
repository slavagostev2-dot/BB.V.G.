from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bbvg.storage import event_id_from_entry

UTC = timezone.utc
JOIN_PATH = "/api/streamer-wheel/action/join"

KNOWN_REASON_MAP: dict[str, tuple[str, bool, bool, str]] = {
    "promo_code_not_used": (
        "ineligible_promo_code",
        True,
        False,
        "Акция доступна только при регистрации по промокоду стримера",
    ),
}

SUCCESS_STATUSES = {
    "joined",
    "participated",
    "already_joined",
    "already_participating",
    "already_marked_participating",
}
TERMINAL_FAILURE_STATUSES = {
    "ineligible_promo_code",
    "authorization_required",
    "referral_ineligible",
    "participation_closed",
    "not_eligible",
    "rejected",
    "unknown_betboom_error",
}
TRANSIENT_FAILURE_STATUSES = {
    "browser_error",
    "button_not_found",
    "unconfirmed",
    "timeout",
    "navigation_timeout",
    "page_timeout",
    "technical_error",
    "rate_limited",
    "server_error",
    "server_acknowledged_unverified",
}


@dataclass(frozen=True)
class JoinOutcome:
    observed: bool
    success: bool
    status: str
    reason: str = ""
    message: str = ""
    http_status: int | None = None
    app_code: int | None = None
    api_status: str = ""
    captured_at: str = ""
    terminal: bool = False
    retry_allowed: bool = False

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _flatten_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    candidates = [body.get("error.message"), body.get("message")]
    error = body.get("error")
    if isinstance(error, dict):
        candidates.extend((error.get("message"), error.get("detail")))
    for value in candidates:
        text = _text(value)
        if text:
            return text[:500]
    return ""


def _violation_rows(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    error = body.get("error")
    if not isinstance(error, dict):
        return []
    details = error.get("details")
    if not isinstance(details, dict):
        return []
    value = details.get("value")
    if not isinstance(value, dict):
        return []
    raw = value.get("violations")
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    text = _text(raw)
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _reason_and_message(body: Any) -> tuple[str, str]:
    for violation in _violation_rows(body):
        reason = _text(violation.get("reason")).casefold()
        message = _text(violation.get("message"))
        if reason or message:
            return reason, message[:500]
    if not isinstance(body, dict):
        return "", ""
    return _text(body.get("reason")).casefold(), _flatten_message(body)


def _is_join_response(event: Any) -> bool:
    return bool(
        isinstance(event, dict)
        and _text(event.get("kind")) == "response"
        and _text(event.get("method")).upper() == "POST"
        and _text(event.get("url")).split("?", 1)[0].endswith(JOIN_PATH)
    )


def _business_error_status(reason: str, message: str) -> tuple[str, bool, bool, str]:
    normalized_reason = reason.casefold()
    if normalized_reason in KNOWN_REASON_MAP:
        return KNOWN_REASON_MAP[normalized_reason]
    combined = f"{normalized_reason} {message.casefold()}"
    if any(marker in combined for marker in ("unauthor", "auth_required", "not_authorized", "авторизац", "войдите")):
        return "authorization_required", True, False, message or "Требуется авторизация BetBoom"
    if any(marker in combined for marker in ("closed", "expired", "finished", "not_active", "заверш", "закрыт")):
        return "participation_closed", True, False, message or "Участие в колесе закрыто"
    if any(marker in combined for marker in ("not_eligible", "ineligible", "forbidden", "недоступ", "не можете участвовать")):
        return "not_eligible", True, False, message or "Аккаунт не соответствует условиям акции"
    if any(marker in combined for marker in ("already_join", "already_particip", "уже участву")):
        return "already_joined", True, False, message or "BetBoom уже зарегистрировал участие"
    return (
        "unknown_betboom_error",
        True,
        False,
        message or (f"BetBoom отклонил участие: {reason}" if reason else "BetBoom отклонил участие"),
    )


def classify_join_response_event(event: Any) -> JoinOutcome | None:
    if not _is_join_response(event):
        return None
    body = event.get("body") if isinstance(event, dict) else None
    body = body if isinstance(body, dict) else {}
    http_status = _int(event.get("status"))
    app_code = _int(body.get("code"))
    api_status = _text(body.get("status"))
    reason, message = _reason_and_message(body)
    captured_at = _text(event.get("captured_at"))
    effective_code = app_code if app_code is not None else http_status
    status_upper = api_status.upper()
    success_value = body.get("success")

    # Transport/server failures are transient even if the JSON status says ERROR.
    if effective_code == 429 or "rate" in reason or "too many" in message.casefold():
        return JoinOutcome(
            True, False, "rate_limited", reason, message or "BetBoom временно ограничил запросы",
            http_status, app_code, api_status, captured_at, False, True,
        )
    if effective_code is not None and effective_code >= 500:
        return JoinOutcome(
            True, False, "server_error", reason, message or "Временная ошибка BetBoom",
            http_status, app_code, api_status, captured_at, False, True,
        )

    # Business 4xx/refusal responses are authoritative terminal outcomes.
    if (
        reason
        or (effective_code is not None and 400 <= effective_code < 500)
        or status_upper in {"BAD_REQUEST", "FAILED", "FAILURE"}
    ):
        mapped, terminal, retry_allowed, fallback = _business_error_status(reason, message)
        if mapped == "already_joined":
            return JoinOutcome(
                True, True, "already_joined", reason, fallback,
                http_status, app_code, api_status, captured_at, True, False,
            )
        return JoinOutcome(
            True, False, mapped, reason, fallback,
            http_status, app_code, api_status, captured_at, terminal, retry_allowed,
        )

    if success_value is True or status_upper in {"OK", "SUCCESS", "SUCCEEDED"} or (
        effective_code is not None
        and 200 <= effective_code < 300
        and not body.get("error")
    ):
        return JoinOutcome(
            True, True, "joined", reason, message or "BetBoom принял запрос на участие",
            http_status, app_code, api_status, captured_at, True, False,
        )

    return JoinOutcome(
        True, False, "unknown_betboom_error", reason,
        message or "Ответ BetBoom на участие не распознан",
        http_status, app_code, api_status, captured_at, True, False,
    )


def latest_join_outcome(events: list[dict[str, Any]]) -> JoinOutcome | None:
    for event in reversed(list(events or [])):
        result = classify_join_response_event(event)
        if result is not None:
            return result
    return None


def human_detail(outcome: JoinOutcome) -> str:
    reason = f"; betboom_reason={outcome.reason}" if outcome.reason else ""
    if outcome.status == "ineligible_promo_code":
        return f"BetBoom отказал в участии: {outcome.message}{reason}"[:300]
    if outcome.success:
        return f"BetBoom API подтвердил участие: {outcome.message}{reason}"[:300]
    return f"BetBoom API: {outcome.message}{reason}"[:300]


def failure_title(status: str) -> str:
    labels = {
        "ineligible_promo_code": "Акция требует промокод стримера",
        "authorization_required": "Требуется авторизация BetBoom",
        "referral_ineligible": "Аккаунт не соответствует реферальному условию",
        "participation_closed": "Участие в колесе закрыто",
        "not_eligible": "Аккаунт не соответствует условиям акции",
        "rejected": "BetBoom отклонил участие",
        "unknown_betboom_error": "BetBoom отклонил участие по неизвестной причине",
        "rate_limited": "BetBoom временно ограничил запросы",
        "server_error": "Временная ошибка BetBoom",
    }
    return labels.get(str(status or "").casefold(), "Автоучастие не подтверждено")


def terminal_manual_action_needed(status: str) -> bool:
    return str(status or "").casefold() not in {
        "ineligible_promo_code",
        "participation_closed",
        "not_eligible",
        "rejected",
        "unknown_betboom_error",
    }


def write_join_result(
    target: Any,
    outcome: JoinOutcome | None,
    *,
    account_key: str = "",
    identifier: str = "",
) -> None:
    if outcome is None or not target:
        return
    path = Path(str(target))
    if not path.is_dir():
        return
    payload = outcome.public_dict()
    payload.update(
        {
            "written_at": datetime.now(UTC).isoformat(),
            "account_key": str(account_key or ""),
            "identifier": str(identifier or ""),
        }
    )
    try:
        (path / "join_result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def register_runtime_terminal_statuses() -> None:
    try:
        import betboom_account_participation as account2
    except Exception:
        return
    account2.TERMINAL_FAILURE_STATUSES.update(TERMINAL_FAILURE_STATUSES)
    account2.TRANSIENT_STATUSES.update(TRANSIENT_FAILURE_STATUSES)


def record_statistics(
    state: dict[str, Any],
    *,
    identifier: str,
    account_key: str,
    status: str,
    reason: str = "",
    observed_at: str = "",
) -> None:
    stats = state.setdefault("auto_participation_outcome_stats", {})
    by_identifier = stats.setdefault("by_identifier", {})
    by_reason = stats.setdefault("by_reason", {})
    key = str(identifier or "unknown").casefold()
    bucket = by_identifier.setdefault(key, {})
    bucket["attempts"] = int(bucket.get("attempts", 0)) + 1
    if status in SUCCESS_STATUSES:
        bucket["successes"] = int(bucket.get("successes", 0)) + 1
    if status in TERMINAL_FAILURE_STATUSES:
        bucket["terminal_failures"] = int(bucket.get("terminal_failures", 0)) + 1
    if status == "ineligible_promo_code":
        bucket["promo_code_restrictions"] = int(bucket.get("promo_code_restrictions", 0)) + 1
    bucket["last_status"] = status
    bucket["last_account_key"] = account_key
    if observed_at:
        bucket["last_observed_at"] = observed_at
    if reason:
        reason_bucket = by_reason.setdefault(reason, {})
        reason_bucket["count"] = int(reason_bucket.get("count", 0)) + 1
        reason_bucket["last_identifier"] = key
        reason_bucket["last_account_key"] = account_key
        if observed_at:
            reason_bucket["last_observed_at"] = observed_at


def _record_is_terminal_failure(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and str(record.get("status") or "").casefold() in TERMINAL_FAILURE_STATUSES
    )


def finalize_event_account_summary(
    state: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
    if not key:
        return {}
    event_id = event_id_from_entry(item, wheel_key=key)
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return {}
    registry = state.get("auto_participation_account_registry")
    expected = {
        str(account_key)
        for account_key, raw in (
            registry.items() if isinstance(registry, dict) else []
        )
        if isinstance(raw, dict) and raw.get("enabled", True)
    }
    results: dict[str, dict[str, Any]] = {}
    for token, raw in events.items():
        if not isinstance(raw, dict):
            continue
        raw_event = str(
            raw.get("event_token") or str(token).split("#account:", 1)[0]
        )
        if raw_event != event_id:
            continue
        account_key = str(raw.get("account_key") or "vyacheslav_primary")
        results[account_key] = {
            "account_label": str(raw.get("account_label") or account_key),
            "status": str(raw.get("status") or ""),
            "detail": str(
                raw.get("detail") or raw.get("bot_failure_detail") or ""
            )[:300],
            "betboom_reason": str(raw.get("betboom_reason") or ""),
            "retry_allowed": bool(raw.get("retry_allowed")),
            "terminal_failure": _record_is_terminal_failure(raw),
            "attempted_at": str(raw.get("attempted_at") or ""),
        }
    settled_expected = bool(expected and expected.issubset(results))
    all_terminal_failure = bool(
        settled_expected
        and all(results[account]["terminal_failure"] for account in expected)
    )
    active = state.get("active_wheels")
    entry = active.get(key) if isinstance(active, dict) else None
    if isinstance(entry, dict):
        entry["auto_participation_account_results"] = results
        entry["auto_participation_all_accounts_settled"] = settled_expected
        entry["auto_participation_terminal"] = all_terminal_failure
        if all_terminal_failure:
            entry["auto_participation_terminal_reason"] = (
                "all_accounts_terminal_failure"
            )
        else:
            entry.pop("auto_participation_terminal_reason", None)
    return {
        "event_id": event_id,
        "expected_accounts": sorted(expected),
        "results": results,
        "all_accounts_settled": settled_expected,
        "all_accounts_terminal_failure": all_terminal_failure,
    }


def self_test() -> None:
    incident = {
        "captured_at": "2026-09-07T19:14:43.948188+00:00",
        "kind": "response",
        "method": "POST",
        "url": "https://betboom.ru/api/streamer-wheel/action/join",
        "status": 200,
        "body": {
            "code": 400,
            "status": "BAD_REQUEST",
            "error": {
                "message": "Акция доступна только при регистрации по промокоду стримера",
                "details": {
                    "value": {
                        "violations": "[{'reason': 'promo_code_not_used', 'message': 'Акция доступна только при регистрации по промокоду стримера'}]"
                    }
                },
            },
            "error.message": "Акция доступна только при регистрации по промокоду стримера",
        },
    }
    outcome = classify_join_response_event(incident)
    assert outcome is not None
    assert outcome.status == "ineligible_promo_code"
    assert outcome.reason == "promo_code_not_used"
    assert outcome.terminal is True
    assert outcome.retry_allowed is False
    assert "промокоду" in human_detail(outcome).casefold()

    temporary = classify_join_response_event(
        {
            "kind": "response",
            "method": "POST",
            "url": "https://betboom.ru/api/streamer-wheel/action/join",
            "status": 503,
            "body": {"code": 503, "status": "ERROR"},
        }
    )
    assert temporary is not None
    assert temporary.status == "server_error"
    assert temporary.retry_allowed

    unknown = classify_join_response_event(
        {
            "kind": "response",
            "method": "POST",
            "url": "https://betboom.ru/api/streamer-wheel/action/join",
            "status": 200,
            "body": {
                "code": 400,
                "status": "BAD_REQUEST",
                "reason": "new_reason",
            },
        }
    )
    assert unknown is not None
    assert unknown.status == "unknown_betboom_error"
    assert unknown.reason == "new_reason"
    print("BetBoom authoritative join outcome self-test passed")


if __name__ == "__main__":
    self_test()
