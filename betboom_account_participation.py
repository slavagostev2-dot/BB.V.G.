from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import auto_participation_owner_sync
import betboom_auto_participation as primary_auto
import betboom_participation_browser
import monitor
import personal_wheel_voting


UTC = timezone.utc
ACCOUNT_KEY = "vyacheslav_secondary"
DEFAULT_ACCOUNT_LABEL = "Аккаунт 2"
DEFAULT_ALERT_USER = "Вячеслав"
XFLARXX_ACCOUNT_KEY = "xflarxx_primary"
DEFAULT_XFLARXX_ACCOUNT_LABEL = "xFLARXx"
DEFAULT_XFLARXX_ALERT_USER = "xFLARXx"
DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")
TRANSIENT_STATUSES = {
    "browser_error",
    "unconfirmed",
    "timeout",
    "navigation_timeout",
    "page_timeout",
}
TERMINAL_FAILURE_STATUSES = {
    "button_not_found",
    "participation_closed",
    "not_eligible",
    "rejected",
}
RETRY_DELAY_MINUTES = 3
MAX_COMPLETED_EVENTS = 500


def account_label() -> str:
    return (
        os.getenv("BETBOOM_ACCOUNT2_LABEL", DEFAULT_ACCOUNT_LABEL).strip()
        or DEFAULT_ACCOUNT_LABEL
    )


def alert_user() -> str:
    return (
        os.getenv("BETBOOM_ACCOUNT2_TELEGRAM_USER", DEFAULT_ALERT_USER).strip()
        or DEFAULT_ALERT_USER
    )


def xflarxx_account_label() -> str:
    return (
        os.getenv("BETBOOM_ACCOUNT3_LABEL", DEFAULT_XFLARXX_ACCOUNT_LABEL).strip()
        or DEFAULT_XFLARXX_ACCOUNT_LABEL
    )


def xflarxx_alert_user() -> str:
    return (
        os.getenv("BETBOOM_ACCOUNT3_TELEGRAM_USER", DEFAULT_XFLARXX_ALERT_USER).strip()
        or DEFAULT_XFLARXX_ALERT_USER
    )


def _storage_state_raw() -> str:
    part3 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART3", "")
    part4 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART4", "")
    return part3 + part4 if part3 or part4 else ""


def storage_state() -> dict[str, Any] | None:
    raw = _storage_state_raw()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def configured() -> bool:
    return storage_state() is not None


def _xflarxx_storage_state_raw() -> str:
    part5 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART5", "")
    part6 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART6", "")
    return part5 + part6 if part5 or part6 else ""


def xflarxx_storage_state() -> dict[str, Any] | None:
    raw = _xflarxx_storage_state_raw()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def xflarxx_configured() -> bool:
    return xflarxx_storage_state() is not None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _base_event_token(item: dict[str, Any], wheel_key: str = "") -> str:
    key = str(
        wheel_key
        or item.get("wheel_key")
        or item.get("identifier")
        or ""
    ).casefold()
    try:
        action_id = int(item.get("action_id") or 0)
    except (TypeError, ValueError):
        action_id = 0
    start = str(item.get("server_start_at") or "")
    if action_id > 0:
        return f"{key}#action:{action_id}:{start}"
    return f"{key}#seen:{item.get('message_date') or ''}"


def _account_event_token(
    item: dict[str, Any],
    wheel_key: str = "",
    account_key: str = ACCOUNT_KEY,
) -> str:
    return f"{_base_event_token(item, wheel_key)}#account:{account_key}"


def _load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default
    return value


def _load_last_json_object(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        value = line.strip()
        if not value:
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _candidate_rows(
    state: dict[str, Any], recovery_result_path: Path
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    recovery = _load_last_json_object(recovery_result_path)
    checked = recovery.get("checked") if isinstance(recovery.get("checked"), list) else []
    for raw in checked:
        if not isinstance(raw, dict) or str(raw.get("api_status") or "") != "active":
            continue
        key = str(raw.get("wheel_key") or "").casefold()
        url = str(raw.get("url") or "").strip()
        if key and url:
            rows[key] = dict(raw)

    active = state.get("active_wheels") if isinstance(state.get("active_wheels"), dict) else {}
    for raw_key, raw in active.items():
        if not isinstance(raw, dict):
            continue
        key = str(raw_key or raw.get("wheel_key") or "").casefold()
        url = str(raw.get("url") or "").strip()
        if not key or not url:
            continue
        page_status = str(raw.get("page_status") or "").casefold()
        if page_status in {"not_started", "finished", "closed", "expired"}:
            continue
        row = dict(raw)
        row.setdefault("wheel_key", key)
        rows.setdefault(key, row)
    return list(rows.values())


def _should_attempt(previous: Any, current: datetime) -> bool:
    if not isinstance(previous, dict):
        return True
    status = str(previous.get("status") or "").casefold()
    if status in {"participated", "already_participating"}:
        return False
    if status in TERMINAL_FAILURE_STATUSES:
        return False
    retry_after = _parse_datetime(previous.get("retry_after_at"))
    if retry_after is not None and retry_after > current:
        return False
    return status in TRANSIENT_STATUSES or not status


def _participate_with_storage(
    url: str, state_value: dict[str, Any]
) -> primary_auto.ParticipationResult:
    original_storage = primary_auto._storage_state
    primary_auto._storage_state = lambda: state_value
    try:
        return betboom_participation_browser.participate(url)
    finally:
        primary_auto._storage_state = original_storage


def run_second_account(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    session = storage_state()
    if session is None:
        raise RuntimeError(
            "Второй BetBoom-аккаунт не настроен: проверьте PART3/PART4"
        )

    state = _load_json(monitor.STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
    attempted = 0
    succeeded = 0
    terminal_failed = 0
    deferred = 0
    skipped = 0

    for item in _candidate_rows(state, recovery_result_path):
        key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
        url = str(item.get("url") or "").strip()
        if not key or not url:
            continue
        token = _account_event_token(item, key)
        previous = events.get(token)
        if not _should_attempt(previous, current):
            skipped += 1
            continue

        attempted += 1
        result = _participate_with_storage(url, session)
        record: dict[str, Any] = {
            "wheel_key": key,
            "event_token": _base_event_token(item, key),
            "account_key": ACCOUNT_KEY,
            "account_label": account_label(),
            "alert_user": alert_user(),
            "status": str(result.status),
            "detail": str(result.detail)[:300],
            "attempted_at": current.isoformat(),
            "retry_allowed": False,
            "multi_account_version": 1,
        }

        if result.success:
            record["status"] = "participated"
            record["bot_success_pending_at"] = current.isoformat()
            record["bot_success_sync_status"] = "waiting_for_control_center"
            record["bot_success_sync_version"] = 1
            succeeded += 1
        elif str(result.status).casefold() in TERMINAL_FAILURE_STATUSES:
            record["bot_failure_pending_at"] = current.isoformat()
            record["bot_failure_sync_status"] = "waiting_for_control_center"
            record["bot_failure_sync_version"] = 1
            record["bot_failure_status"] = str(result.status)[:80]
            record["bot_failure_detail"] = str(result.detail)[:300]
            terminal_failed += 1
        else:
            record["retry_allowed"] = True
            record["retry_after_at"] = (
                current + timedelta(minutes=RETRY_DELAY_MINUTES)
            ).isoformat()
            record["user_alert_policy"] = "deferred_transient_failure"
            deferred += 1

        events[token] = record

    state["last_secondary_account_participation_at"] = current.isoformat()
    monitor.save_state(state)
    return {
        "account_key": ACCOUNT_KEY,
        "account_label": account_label(),
        "alert_user": alert_user(),
        "attempted": attempted,
        "succeeded": succeeded,
        "terminal_failed": terminal_failed,
        "deferred": deferred,
        "skipped": skipped,
    }


def run_xflarxx_account(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    session = xflarxx_storage_state()
    if session is None:
        raise RuntimeError(
            "BetBoom-аккаунт xFLARXx не настроен: проверьте PART5/PART6"
        )

    state = _load_json(monitor.STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
    attempted = 0
    succeeded = 0
    terminal_failed = 0
    deferred = 0
    skipped = 0

    for item in _candidate_rows(state, recovery_result_path):
        key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
        url = str(item.get("url") or "").strip()
        if not key or not url:
            continue
        token = _account_event_token(item, key, XFLARXX_ACCOUNT_KEY)
        previous = events.get(token)
        if not _should_attempt(previous, current):
            skipped += 1
            continue

        attempted += 1
        result = _participate_with_storage(url, session)
        record: dict[str, Any] = {
            "wheel_key": key,
            "event_token": _base_event_token(item, key),
            "account_key": XFLARXX_ACCOUNT_KEY,
            "account_label": xflarxx_account_label(),
            "alert_user": xflarxx_alert_user(),
            "status": str(result.status),
            "detail": str(result.detail)[:300],
            "attempted_at": current.isoformat(),
            "retry_allowed": False,
            "multi_account_version": 2,
        }

        if result.success:
            record["status"] = "participated"
            record["bot_success_pending_at"] = current.isoformat()
            record["bot_success_sync_status"] = "waiting_for_control_center"
            record["bot_success_sync_version"] = 1
            succeeded += 1
        elif str(result.status).casefold() in TERMINAL_FAILURE_STATUSES:
            record["bot_failure_pending_at"] = current.isoformat()
            record["bot_failure_sync_status"] = "waiting_for_control_center"
            record["bot_failure_sync_version"] = 1
            record["bot_failure_status"] = str(result.status)[:80]
            record["bot_failure_detail"] = str(result.detail)[:300]
            terminal_failed += 1
        else:
            record["retry_allowed"] = True
            record["retry_after_at"] = (
                current + timedelta(minutes=RETRY_DELAY_MINUTES)
            ).isoformat()
            record["user_alert_policy"] = "deferred_transient_failure"
            deferred += 1

        events[token] = record

    state["last_xflarxx_account_participation_at"] = current.isoformat()
    monitor.save_state(state)
    return {
        "account_key": XFLARXX_ACCOUNT_KEY,
        "account_label": xflarxx_account_label(),
        "alert_user": xflarxx_alert_user(),
        "attempted": attempted,
        "succeeded": succeeded,
        "terminal_failed": terminal_failed,
        "deferred": deferred,
        "skipped": skipped,
    }


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


def _target_context(
    panel: Any, target: str
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    access = panel.load_access(force=True)
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    normalized_target = str(target or "").strip().casefold()
    for user_id, raw in users.items():
        if not isinstance(raw, dict):
            continue
        names = _normalized_names(str(user_id), raw)
        if normalized_target in names or any(
            value == normalized_target or value.startswith(normalized_target + " ")
            for value in names
        ):
            chat_id = str(raw.get("chat_id") or user_id).strip()
            if chat_id:
                return access, str(user_id), raw, chat_id
    raise RuntimeError(
        f"Telegram-пользователь {target!r} не найден в зашифрованном состоянии"
    )


def _outcome_records(user: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    raw = user.get(field)
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): dict(value) if isinstance(value, dict) else {}
        for key, value in raw.items()
        if str(key)
    }


def _save_outcome(
    panel: Any,
    user_id: str,
    *,
    field: str,
    event_key: str,
    payload: dict[str, Any],
) -> None:
    access = panel.load_access(force=True)
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    user = users.get(user_id) if isinstance(users.get(user_id), dict) else None
    if not isinstance(user, dict):
        raise RuntimeError("Профиль Telegram-пользователя не найден")
    records = _outcome_records(user, field)
    records[event_key] = dict(payload)
    if len(records) > MAX_COMPLETED_EVENTS:
        records = dict(list(records.items())[-MAX_COMPLETED_EVENTS:])
    user[field] = records
    panel.access = access
    panel.access_loaded = True
    panel.save_access("Record secondary BetBoom account outcome [skip ci]")


def _pending_account_events(
    state: dict[str, Any], field: str
) -> list[tuple[str, dict[str, Any]]]:
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for token, raw in events.items():
        if not isinstance(raw, dict) or str(raw.get("account_key") or "") != ACCOUNT_KEY:
            continue
        if not raw.get(field):
            continue
        result.append((str(token), raw))
    result.sort(key=lambda item: str(item[1].get(field) or ""))
    return result


def _short_message(
    success: bool, key: str, item: dict[str, Any], record: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    title = "✅ <b>Участие принято</b>" if success else "⚠️ <b>Участие не принято</b>"
    label = html.escape(str(record.get("account_label") or DEFAULT_ACCOUNT_LABEL))
    identifier = html.escape(str(item.get("identifier") or key))
    return (
        f"{title}\n\n"
        f"Аккаунт: <b>{label}</b>\n"
        f"Колесо: <code>{identifier}</code>",
        {
            "inline_keyboard": [
                [
                    {"text": "🔥 Активные колёса", "callback_data": "bb:l:active"},
                    {"text": "🏠 Главное меню", "callback_data": "page:menu"},
                ]
            ]
        },
    )


def sync_account_events(panel: Any) -> dict[str, int]:
    snap = panel.snapshot()
    state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
    success_candidates = _pending_account_events(state, "bot_success_pending_at")
    failure_candidates = _pending_account_events(state, "bot_failure_pending_at")
    if not success_candidates and not failure_candidates:
        return {"pending": 0, "completed": 0, "failed": 0}

    active = state.get("active_wheels") if isinstance(state.get("active_wheels"), dict) else {}
    completed = 0
    failed = 0
    original_context = (
        getattr(panel, "current_chat_id", None),
        getattr(panel, "current_user_id", None),
        getattr(panel, "current_role", "guest"),
    )

    for success, candidates in ((True, success_candidates), (False, failure_candidates)):
        for token, record in candidates:
            key = str(record.get("wheel_key") or "").casefold()
            item = active.get(key)
            if not key or not isinstance(item, dict):
                failed += 1
                continue
            if str(record.get("event_token") or "") != _base_event_token(item, key):
                continue
            try:
                _access, user_id, user, chat_id = _target_context(
                    panel, str(record.get("alert_user") or DEFAULT_ALERT_USER)
                )
                outcome_key = (
                    personal_wheel_voting.wheel_event_key(key, item)
                    + f"#account:{ACCOUNT_KEY}"
                )
                field = (
                    "auto_participation_success_events"
                    if success
                    else "auto_participation_failure_events"
                )
                previous = _outcome_records(user, field).get(outcome_key)
                if isinstance(previous, dict) and previous.get("notified_at"):
                    continue

                panel.set_context(chat_id, user_id)
                vote_result: dict[str, Any] = {}
                if success:
                    raw_result = panel.mark_personal_participation(key)
                    vote_result = raw_result if isinstance(raw_result, dict) else {}
                    auto_participation_owner_sync._mark_original_notification(
                        panel, chat_id, item
                    )
                text, markup = _short_message(success, key, item, record)
                panel.send(text, reply_markup=markup, chat_id=chat_id)
                now_text = datetime.now(UTC).isoformat()
                _save_outcome(
                    panel,
                    user_id,
                    field=field,
                    event_key=outcome_key,
                    payload={
                        "wheel_key": key,
                        "source_event_token": token,
                        "account_key": ACCOUNT_KEY,
                        "account_label": str(record.get("account_label") or ""),
                        "notified_at": now_text,
                        "vote_changed": bool(vote_result.get("changed")),
                        "vote_command_id": str(vote_result.get("vote_command_id") or ""),
                    },
                )
                completed += 1
            except Exception as exc:
                failed += 1
                print(
                    "WARNING secondary BetBoom account sync: "
                    f"wheel={key} {type(exc).__name__}: {exc}"
                )
            finally:
                panel.current_chat_id, panel.current_user_id, panel.current_role = (
                    original_context
                )

    return {
        "pending": len(success_candidates) + len(failure_candidates),
        "completed": completed,
        "failed": failed,
    }


def install_owner_sync() -> None:
    if getattr(auto_participation_owner_sync, "_bbvg_account_sync_installed", False):
        return
    original_sync_once = auto_participation_owner_sync.sync_once

    def sync_once_with_accounts(panel: Any) -> dict[str, int]:
        base = dict(original_sync_once(panel))
        extra = sync_account_events(panel)
        base["pending"] = int(base.get("pending", 0)) + int(extra.get("pending", 0))
        base["completed"] = int(base.get("completed", 0)) + int(extra.get("completed", 0))
        base["failed"] = int(base.get("failed", 0)) + int(extra.get("failed", 0))
        base["account_completed"] = int(extra.get("completed", 0))
        return base

    auto_participation_owner_sync.sync_once = sync_once_with_accounts
    auto_participation_owner_sync._bbvg_account_sync_installed = True


def self_test() -> None:
    previous3 = os.environ.get("BETBOOM_STORAGE_STATE_JSON_PART3")
    previous4 = os.environ.get("BETBOOM_STORAGE_STATE_JSON_PART4")
    try:
        raw = json.dumps({"cookies": [], "origins": []}, separators=(",", ":"))
        middle = len(raw) // 2
        os.environ["BETBOOM_STORAGE_STATE_JSON_PART3"] = raw[:middle]
        os.environ["BETBOOM_STORAGE_STATE_JSON_PART4"] = raw[middle:]
        assert configured()
        assert storage_state() == {"cookies": [], "origins": []}
    finally:
        if previous3 is None:
            os.environ.pop("BETBOOM_STORAGE_STATE_JSON_PART3", None)
        else:
            os.environ["BETBOOM_STORAGE_STATE_JSON_PART3"] = previous3
        if previous4 is None:
            os.environ.pop("BETBOOM_STORAGE_STATE_JSON_PART4", None)
        else:
            os.environ["BETBOOM_STORAGE_STATE_JSON_PART4"] = previous4

    previous5 = os.environ.get("BETBOOM_STORAGE_STATE_JSON_PART5")
    previous6 = os.environ.get("BETBOOM_STORAGE_STATE_JSON_PART6")
    try:
        raw = json.dumps({"cookies": [], "origins": []}, separators=(",", ":"))
        middle = len(raw) // 2
        os.environ["BETBOOM_STORAGE_STATE_JSON_PART5"] = raw[:middle]
        os.environ["BETBOOM_STORAGE_STATE_JSON_PART6"] = raw[middle:]
        assert xflarxx_configured()
        assert xflarxx_storage_state() == {"cookies": [], "origins": []}
    finally:
        if previous5 is None:
            os.environ.pop("BETBOOM_STORAGE_STATE_JSON_PART5", None)
        else:
            os.environ["BETBOOM_STORAGE_STATE_JSON_PART5"] = previous5
        if previous6 is None:
            os.environ.pop("BETBOOM_STORAGE_STATE_JSON_PART6", None)
        else:
            os.environ["BETBOOM_STORAGE_STATE_JSON_PART6"] = previous6

    item = {
        "wheel_key": "wheel",
        "action_id": 42,
        "server_start_at": "2026-07-22T12:00:00+00:00",
    }
    assert _base_event_token(item) == "wheel#action:42:2026-07-22T12:00:00+00:00"
    assert _account_event_token(item).endswith("#account:vyacheslav_secondary")
    assert _account_event_token(
        item, account_key=XFLARXX_ACCOUNT_KEY
    ).endswith("#account:xflarxx_primary")
    assert not _should_attempt(
        {"status": "participated"}, datetime(2026, 7, 22, tzinfo=UTC)
    )
    assert _should_attempt(
        {
            "status": "browser_error",
            "retry_after_at": "2026-07-21T00:00:00+00:00",
        },
        datetime(2026, 7, 22, tzinfo=UTC),
    )
    install_owner_sync()
    assert auto_participation_owner_sync._bbvg_account_sync_installed is True
    print("secondary BetBoom account self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--recovery-result",
        type=Path,
        default=DEFAULT_RECOVERY_RESULT,
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    results = [
        run_second_account(args.recovery_result),
        run_xflarxx_account(args.recovery_result),
    ]
    print(json.dumps({"accounts": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
