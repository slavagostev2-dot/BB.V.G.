from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import auto_participation_owner_sync
import betboom_account_participation as account_base
import monitor
import personal_wheel_voting
import wheel_publications_v2

UTC = timezone.utc
ACCOUNT_KEY = "xflarxx_primary"
DEFAULT_ACCOUNT_LABEL = "xFLARXx"
DEFAULT_ALERT_USER = "xFLARXx"
DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")
RETRY_DELAY_MINUTES = account_base.RETRY_DELAY_MINUTES
TERMINAL_FAILURE_STATUSES = account_base.TERMINAL_FAILURE_STATUSES
SUCCESS_STATUSES = {
    "participated",
    "already_participating",
    "already_marked_participating",
    "already_marked_in_bot",
}


def account_label() -> str:
    return (
        os.getenv("BETBOOM_ACCOUNT3_LABEL", DEFAULT_ACCOUNT_LABEL).strip()
        or DEFAULT_ACCOUNT_LABEL
    )


def alert_user() -> str:
    return (
        os.getenv("BETBOOM_ACCOUNT3_TELEGRAM_USER", DEFAULT_ALERT_USER).strip()
        or DEFAULT_ALERT_USER
    )


def _storage_state_raw() -> str:
    part5 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART5", "")
    part6 = os.getenv("BETBOOM_STORAGE_STATE_JSON_PART6", "")
    return part5 + part6 if part5 or part6 else ""


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


def _account_event_token(item: dict[str, Any], wheel_key: str = "") -> str:
    return f"{account_base._base_event_token(item, wheel_key)}#account:{ACCOUNT_KEY}"


def run_account(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    session = storage_state()
    if session is None:
        raise RuntimeError(
            "BetBoom-аккаунт xFLARXx не настроен: проверьте PART5/PART6"
        )

    state = account_base._load_json(monitor.STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
    attempted = 0
    succeeded = 0
    terminal_failed = 0
    deferred = 0
    skipped = 0

    for item in account_base._candidate_rows(state, recovery_result_path):
        key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
        url = str(item.get("url") or "").strip()
        if not key or not url:
            continue
        token = _account_event_token(item, key)
        previous = events.get(token)
        if not account_base._should_attempt(previous, current):
            skipped += 1
            continue

        attempted += 1
        result = account_base._participate_with_storage(url, session)
        record: dict[str, Any] = {
            "wheel_key": key,
            "event_token": account_base._base_event_token(item, key),
            "account_key": ACCOUNT_KEY,
            "account_label": account_label(),
            "alert_user": alert_user(),
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
        "account_key": ACCOUNT_KEY,
        "account_label": account_label(),
        "alert_user": alert_user(),
        "attempted": attempted,
        "succeeded": succeeded,
        "terminal_failed": terminal_failed,
        "deferred": deferred,
        "skipped": skipped,
    }


def _pending_records(
    state: dict[str, Any],
) -> list[tuple[str, dict[str, Any], bool]]:
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return []
    approved_failures = {
        token
        for token, _record in auto_participation_owner_sync.pending_failure_events(state)
    }
    result: list[tuple[str, dict[str, Any], bool]] = []
    for raw_token, raw_record in events.items():
        if not isinstance(raw_record, dict):
            continue
        if str(raw_record.get("account_key") or "") != ACCOUNT_KEY:
            continue
        token = str(raw_token)
        success = str(raw_record.get("status") or "").casefold() in SUCCESS_STATUSES
        if success or token in approved_failures:
            result.append((token, raw_record, success))
    result.sort(key=lambda row: str(row[1].get("attempted_at") or row[0]))
    return result


def _notification_enabled(user: dict[str, Any]) -> bool:
    preferences = user.get("notification_preferences")
    if not isinstance(preferences, dict):
        return True
    return bool(preferences.get("auto_participation", True))


def _should_send_notification(user: dict[str, Any], item: dict[str, Any]) -> bool:
    return _notification_enabled(user) and not (
        wheel_publications_v2.entry_is_referral_restricted(item)
    )


def _failure_reason(record: dict[str, Any]) -> str:
    status = str(
        record.get("bot_failure_status") or record.get("status") or ""
    ).casefold()
    labels = {
        "button_not_found": "кнопка участия не найдена",
        "participation_closed": "участие уже закрыто",
        "not_eligible": "аккаунт не подходит",
        "rejected": "BetBoom отклонил участие",
    }
    if status in labels:
        return labels[status]
    return str(
        record.get("bot_failure_detail")
        or record.get("detail")
        or "участие не подтверждено"
    )[:120]


def _message(
    key: str,
    item: dict[str, Any],
    record: dict[str, Any],
    success: bool,
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    label = html.escape(str(record.get("account_label") or DEFAULT_ACCOUNT_LABEL))
    if success:
        text = (
            "✅ <b>Участие принято</b>\n\n"
            f"Колесо: <code>{identifier}</code>\n"
            f"Аккаунт: <b>{label}</b>"
        )
    else:
        text = (
            "⚠️ <b>Участие не принято</b>\n\n"
            f"Колесо: <code>{identifier}</code>\n"
            f"Аккаунт: <b>{label}</b>\n"
            f"Причина: {html.escape(_failure_reason(record))}"
        )
    return (
        text,
        {
            "inline_keyboard": [[
                {"text": "🔥 Активные колёса", "callback_data": "bb:l:active"},
                {"text": "🏠 Главное меню", "callback_data": "page:menu"},
            ]]
        },
    )


def sync_account_events(panel: Any) -> dict[str, int]:
    snap = panel.snapshot()
    state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
    candidates = _pending_records(state)
    if not candidates:
        return {"pending": 0, "completed": 0, "failed": 0}

    active = state.get("active_wheels") if isinstance(state.get("active_wheels"), dict) else {}
    completed = 0
    failed = 0
    original_context = (
        getattr(panel, "current_chat_id", None),
        getattr(panel, "current_user_id", None),
        getattr(panel, "current_role", "guest"),
    )

    for token, record, success in candidates:
        key = str(record.get("wheel_key") or "").casefold()
        item = active.get(key)
        if not key or not isinstance(item, dict):
            failed += 1
            continue
        if str(record.get("event_token") or "") != account_base._base_event_token(item, key):
            continue
        try:
            _access, user_id, user, chat_id = account_base._target_context(
                panel, str(record.get("alert_user") or DEFAULT_ALERT_USER)
            )
            event_key = (
                personal_wheel_voting.wheel_event_key(key, item)
                + f"#account:{ACCOUNT_KEY}"
            )
            field = (
                "auto_participation_success_events"
                if success
                else "auto_participation_failure_events"
            )
            previous = account_base._outcome_records(user, field).get(event_key)
            if isinstance(previous, dict) and (
                previous.get("completed_at") or previous.get("notified_at")
            ):
                continue

            panel.set_context(chat_id, user_id)
            vote_result: dict[str, Any] = {}
            original_button_updated = False
            if success:
                raw_result = panel.mark_personal_participation(key)
                vote_result = raw_result if isinstance(raw_result, dict) else {}
                original_button_updated = auto_participation_owner_sync._mark_original_notification(
                    panel, chat_id, item
                )

            referral_restricted = wheel_publications_v2.entry_is_referral_restricted(item)
            notifications_enabled = _notification_enabled(user)
            should_send = _should_send_notification(user, item)
            now_text = datetime.now(UTC).isoformat()
            if should_send:
                text, markup = _message(key, item, record, success)
                panel.send(text, reply_markup=markup, chat_id=chat_id)

            account_base._save_outcome(
                panel,
                user_id,
                field=field,
                event_key=event_key,
                payload={
                    "wheel_key": key,
                    "source_event_token": token,
                    "account_key": ACCOUNT_KEY,
                    "account_label": str(record.get("account_label") or ""),
                    "completed_at": now_text,
                    "notified_at": now_text if should_send else "",
                    "notification_sent": should_send,
                    "notification_policy": (
                        "sent"
                        if should_send
                        else "disabled"
                        if not notifications_enabled
                        else "referral_suppressed"
                    ),
                    "referral_restricted": referral_restricted,
                    "original_button_updated": original_button_updated,
                    "vote_changed": bool(vote_result.get("changed")),
                    "vote_command_id": str(vote_result.get("vote_command_id") or ""),
                },
            )
            completed += 1
        except Exception as exc:
            failed += 1
            print(
                "WARNING xFLARXx BetBoom account sync: "
                f"wheel={key} {type(exc).__name__}: {exc}"
            )
        finally:
            panel.current_chat_id, panel.current_user_id, panel.current_role = (
                original_context
            )

    return {
        "pending": len(candidates),
        "completed": completed,
        "failed": failed,
    }


def install_owner_sync() -> None:
    if getattr(auto_participation_owner_sync, "_bbvg_xflarxx_sync_installed", False):
        return
    original_sync_once = auto_participation_owner_sync.sync_once

    def sync_once_with_xflarxx(panel: Any) -> dict[str, int]:
        base = dict(original_sync_once(panel))
        extra = sync_account_events(panel)
        base["pending"] = int(base.get("pending", 0)) + int(extra.get("pending", 0))
        base["completed"] = int(base.get("completed", 0)) + int(extra.get("completed", 0))
        base["failed"] = int(base.get("failed", 0)) + int(extra.get("failed", 0))
        base["xflarxx_completed"] = int(extra.get("completed", 0))
        return base

    auto_participation_owner_sync.sync_once = sync_once_with_xflarxx
    auto_participation_owner_sync._bbvg_xflarxx_sync_installed = True


def self_test() -> None:
    previous5 = os.environ.get("BETBOOM_STORAGE_STATE_JSON_PART5")
    previous6 = os.environ.get("BETBOOM_STORAGE_STATE_JSON_PART6")
    try:
        raw = json.dumps({"cookies": [], "origins": []}, separators=(",", ":"))
        middle = len(raw) // 2
        os.environ["BETBOOM_STORAGE_STATE_JSON_PART5"] = raw[:middle]
        os.environ["BETBOOM_STORAGE_STATE_JSON_PART6"] = raw[middle:]
        assert configured()
        assert storage_state() == {"cookies": [], "origins": []}
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
    assert _account_event_token(item).endswith("#account:xflarxx_primary")
    assert _notification_enabled({"notification_preferences": {}})
    assert not _notification_enabled(
        {"notification_preferences": {"auto_participation": False}}
    )
    assert not _should_send_notification(
        {"notification_preferences": {}},
        {"message_text": "Колесо только для рефералов"},
    )
    print("xFLARXx BetBoom account self-test passed")


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
    result = run_account(args.recovery_result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
