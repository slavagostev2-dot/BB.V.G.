from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import betboom_account_participation as account2
import monitor
import wheel_publications_v2
import xflarxx_account_participation as account3


DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")


@dataclass(frozen=True)
class AccountRunConfig:
    name: str
    account_key: str
    account_owner: str
    account_order: int
    multi_account_version: int
    last_run_state_field: str
    session_getter: Callable[[], dict[str, Any] | None]
    label_getter: Callable[[], str]
    alert_user_getter: Callable[[], str]
    missing_session_error: str
    ensure_default_registry: bool = False
    canonicalize_primary_aliases: bool = False


def account_config(name: str) -> AccountRunConfig:
    normalized = str(name or "").strip().casefold()
    if normalized in {"account2", "second", account2.ACCOUNT_KEY.casefold()}:
        return AccountRunConfig(
            name="account2",
            account_key=account2.ACCOUNT_KEY,
            account_owner=account2.ACCOUNT_OWNER,
            account_order=account2.ACCOUNT_ORDER,
            multi_account_version=1,
            last_run_state_field="last_secondary_account_participation_at",
            session_getter=account2.storage_state,
            label_getter=account2.account_label,
            alert_user_getter=account2.alert_user,
            missing_session_error=(
                "Второй BetBoom-аккаунт не настроен: проверьте PART3/PART4"
            ),
            ensure_default_registry=True,
            canonicalize_primary_aliases=True,
        )
    if normalized in {"account3", "xflarxx", account3.ACCOUNT_KEY.casefold()}:
        return AccountRunConfig(
            name="account3",
            account_key=account3.ACCOUNT_KEY,
            account_owner=account3.ACCOUNT_OWNER,
            account_order=account3.ACCOUNT_ORDER,
            multi_account_version=2,
            last_run_state_field="last_xflarxx_account_participation_at",
            session_getter=account3.storage_state,
            label_getter=account3.account_label,
            alert_user_getter=account3.alert_user,
            missing_session_error=(
                "BetBoom-аккаунт xFLARXx не настроен: проверьте PART5/PART6"
            ),
        )
    raise ValueError(f"Неизвестный дополнительный BetBoom-аккаунт: {name!r}")


def _account_event_token(
    config: AccountRunConfig,
    item: dict[str, Any],
    wheel_key: str = "",
) -> str:
    return (
        f"{account2._base_event_token(item, wheel_key)}"
        f"#account:{config.account_key}"
    )


def run_configured_account(
    config: AccountRunConfig,
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    session = config.session_getter()
    if session is None:
        raise RuntimeError(config.missing_session_error)

    state = account2._load_json(monitor.STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    if config.ensure_default_registry:
        account2.primary_auto.ensure_default_account_registry(state)
    account2.primary_auto.register_account(
        state,
        account_key=config.account_key,
        account_label=config.label_getter(),
        account_owner=config.account_owner,
        account_order=config.account_order,
    )
    if config.canonicalize_primary_aliases:
        account2.primary_auto.canonicalize_primary_event_aliases(state)

    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
    attempted = 0
    succeeded = 0
    terminal_failed = 0
    deferred = 0
    skipped = 0

    for item in account2._candidate_rows(state, recovery_result_path):
        key = str(
            item.get("wheel_key") or item.get("identifier") or ""
        ).casefold()
        url = str(item.get("url") or "").strip()
        if not key or not url:
            continue

        token = _account_event_token(config, item, key)
        previous = events.get(token)
        if not account2._should_attempt(previous, current):
            skipped += 1
            continue

        attempted += 1
        result = account2._participate_with_storage(url, session)
        wheel_publications_v2.apply_referral_context(
            state,
            item,
            observed_at=current,
            browser_detail=result.detail,
        )
        record: dict[str, Any] = {
            "wheel_key": key,
            "event_token": account2._base_event_token(item, key),
            "account_key": config.account_key,
            "account_label": config.label_getter(),
            "alert_user": config.alert_user_getter(),
            "account_owner": config.account_owner,
            "account_order": config.account_order,
            "status": str(result.status),
            "detail": str(result.detail)[:300],
            "attempted_at": current.isoformat(),
            "retry_allowed": False,
            "multi_account_version": config.multi_account_version,
            "artifact_url": result.artifact_url,
        }

        if result.success:
            record["status"] = "participated"
            record["bot_success_pending_at"] = current.isoformat()
            record["bot_success_sync_status"] = "waiting_for_control_center"
            record["bot_success_sync_version"] = 1
            succeeded += 1
        elif str(result.status).casefold() in account2.TERMINAL_FAILURE_STATUSES:
            record["bot_failure_pending_at"] = current.isoformat()
            record["bot_failure_sync_status"] = "waiting_for_control_center"
            record["bot_failure_sync_version"] = 1
            record["bot_failure_status"] = str(result.status)[:80]
            record["bot_failure_detail"] = str(result.detail)[:300]
            terminal_failed += 1
        else:
            record["retry_allowed"] = True
            record["retry_after_at"] = (
                current
                + account2.timedelta(minutes=account2.RETRY_DELAY_MINUTES)
            ).isoformat()
            record["user_alert_policy"] = "deferred_transient_failure"
            deferred += 1

        events[token] = account2.primary_auto.merge_event_record(
            events.get(token), record
        )

    state[config.last_run_state_field] = current.isoformat()
    monitor.save_state(state)
    return {
        "account_key": config.account_key,
        "account_label": config.label_getter(),
        "alert_user": config.alert_user_getter(),
        "attempted": attempted,
        "succeeded": succeeded,
        "terminal_failed": terminal_failed,
        "deferred": deferred,
        "skipped": skipped,
    }


def run_account(
    name: str,
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    return run_configured_account(account_config(name), recovery_result_path)


def self_test() -> None:
    second = account_config("account2")
    third = account_config("account3")
    assert second.account_key == "vyacheslav_secondary"
    assert second.multi_account_version == 1
    assert second.ensure_default_registry is True
    assert second.canonicalize_primary_aliases is True
    assert second.last_run_state_field == "last_secondary_account_participation_at"
    assert third.account_key == "xflarxx_primary"
    assert third.multi_account_version == 2
    assert third.ensure_default_registry is False
    assert third.canonicalize_primary_aliases is False
    assert third.last_run_state_field == "last_xflarxx_account_participation_at"
    item = {
        "wheel_key": "wheel",
        "action_id": 42,
        "server_start_at": "2026-07-22T12:00:00+00:00",
    }
    assert _account_event_token(second, item).endswith(
        "#account:vyacheslav_secondary"
    )
    assert _account_event_token(third, item).endswith("#account:xflarxx_primary")
    print("generic secondary BetBoom account runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--account",
        required=True,
        choices=("account2", "account3"),
    )
    parser.add_argument(
        "--recovery-result",
        type=Path,
        default=DEFAULT_RECOVERY_RESULT,
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    result = run_account(args.account, args.recovery_result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
