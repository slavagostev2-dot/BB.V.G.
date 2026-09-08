from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import betboom_account_participation as account2
import betboom_join_outcomes as join_outcomes
import betboom_persistent_participation as persistent
import betboom_profile_identity as profile_identity
import monitor
import wheel_publications_v2
import xflarxx_account_participation as account3


DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")
_REASON_RE = re.compile(r"(?:^|;)\s*betboom_reason=([^;\s]+)", re.IGNORECASE)

# Make v3 BetBoom result classes authoritative for every fresh secondary runner.
join_outcomes.register_runtime_terminal_statuses()


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
            multi_account_version=6,
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
            multi_account_version=6,
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


def _old_post_click_only_success(previous: Any) -> bool:
    if not isinstance(previous, dict):
        return False
    return (
        str(previous.get("status") or "").casefold() == "participated"
        and str(previous.get("confirmation_method") or "").casefold()
        == "betboom_post_click"
    )


def _same_auth_revision(previous: Any, auth_revision: str) -> bool:
    return bool(
        isinstance(previous, dict)
        and auth_revision
        and str(previous.get("auth_revision") or "") == auth_revision
    )


def _should_attempt(
    previous: Any,
    current: Any,
    auth_revision: str,
) -> bool:
    if isinstance(previous, dict) and not _same_auth_revision(previous, auth_revision):
        return True
    if _old_post_click_only_success(previous):
        return True
    return account2._should_attempt(previous, current)


def _causal_fields(result: Any) -> dict[str, Any]:
    status = str(getattr(result, "status", "") or "").casefold()
    detail = str(getattr(result, "detail", "") or "")
    if status == "already_participating":
        return {
            "status": "already_participating",
            "confirmation_method": "betboom_preexisting",
            "participation_origin": "preexisting_verified",
            "clicked_by_bot": False,
        }
    if status == "participated":
        persistence_verified = persistent.PERSISTENCE_VERIFIED_MARKER in detail.casefold()
        server_acknowledged = "server_join_acknowledged=true" in detail.casefold()
        verified = persistence_verified or server_acknowledged
        return {
            "status": "participated" if verified else "unconfirmed",
            "confirmation_method": (
                "betboom_post_reload"
                if persistence_verified
                else "betboom_join_api"
                if server_acknowledged
                else "betboom_post_click"
            ),
            "participation_origin": "automatic" if verified else "unverified",
            "clicked_by_bot": True,
        }
    return {
        "status": status or "unconfirmed",
        "confirmation_method": (
            "betboom_join_api"
            if "betboom_reason=" in detail.casefold() or "betboom api" in detail.casefold()
            else "betboom_unconfirmed"
        ),
        "participation_origin": "unverified",
        "clicked_by_bot": "clicked_by_bot=true" in detail.casefold(),
    }


def _betboom_reason(detail: str) -> str:
    match = _REASON_RE.search(str(detail or ""))
    return str(match.group(1) if match else "").strip().casefold()


def _assert_session_is_distinct(
    config: AccountRunConfig,
    session: dict[str, Any],
) -> None:
    comparisons: list[tuple[str, dict[str, Any] | None]] = [
        ("account1", account2.primary_auto._storage_state()),
    ]
    if config.name == "account3":
        comparisons.append(("account2", account2.storage_state()))
    persistent.assert_distinct_session(config.name, session, *comparisons)


def run_configured_account(
    config: AccountRunConfig,
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    join_outcomes.register_runtime_terminal_statuses()
    session = config.session_getter()
    if session is None:
        raise RuntimeError(config.missing_session_error)
    _assert_session_is_distinct(config, session)
    identity_report = profile_identity.assert_account_slot_distinct(config.account_key)
    auth_revision = profile_identity.account_auth_revision(
        config.account_key,
        identity_report,
    )

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
    state.setdefault("auto_participation_auth_revisions", {})[
        config.account_key
    ] = auth_revision

    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
    attempted = 0
    succeeded = 0
    preexisting = 0
    clicked_successes = 0
    terminal_failed = 0
    deferred = 0
    skipped = 0
    auth_rearmed = 0
    outcomes: list[dict[str, Any]] = []

    for item in account2._candidate_rows(state, recovery_result_path):
        key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
        url = str(item.get("url") or "").strip()
        if not key or not url:
            continue
        if bool(item.get("auto_participation_terminal")):
            skipped += 1
            continue

        token = _account_event_token(config, item, key)
        previous = events.get(token)
        same_revision = _same_auth_revision(previous, auth_revision)
        if not _should_attempt(previous, current, auth_revision):
            skipped += 1
            join_outcomes.finalize_event_account_summary(state, item)
            continue
        if isinstance(previous, dict) and not same_revision:
            auth_rearmed += 1

        attempted += 1
        result = persistent.participate_with_persistence_proof(url, session)
        wheel_publications_v2.apply_referral_context(
            state,
            item,
            observed_at=current,
            browser_detail=result.detail,
        )
        causal = _causal_fields(result)
        reason = _betboom_reason(str(result.detail))
        record: dict[str, Any] = {
            "wheel_key": key,
            "event_token": account2._base_event_token(item, key),
            "account_key": config.account_key,
            "account_label": config.label_getter(),
            "alert_user": config.alert_user_getter(),
            "account_owner": config.account_owner,
            "account_order": config.account_order,
            "auth_revision": auth_revision,
            "status": causal["status"],
            "detail": str(result.detail)[:300],
            "attempted_at": current.isoformat(),
            "retry_allowed": False,
            "multi_account_version": config.multi_account_version,
            "artifact_url": result.artifact_url,
            "confirmation_method": causal["confirmation_method"],
            "participation_origin": causal["participation_origin"],
            "clicked_by_bot": causal["clicked_by_bot"],
        }
        if reason:
            record["betboom_reason"] = reason

        if result.success and causal["status"] in {"participated", "already_participating"}:
            record["bot_success_pending_at"] = current.isoformat()
            record["bot_success_sync_status"] = "waiting_for_control_center"
            record["bot_success_sync_version"] = 1
            succeeded += 1
            if record["status"] == "already_participating":
                preexisting += 1
            elif record["status"] == "participated":
                clicked_successes += 1
        elif str(result.status).casefold() in account2.TERMINAL_FAILURE_STATUSES:
            record["bot_failure_pending_at"] = current.isoformat()
            record["bot_failure_sync_status"] = "waiting_for_control_center"
            record["bot_failure_sync_version"] = 1
            record["bot_failure_status"] = str(result.status)[:80]
            record["bot_failure_detail"] = str(result.detail)[:300]
            record["user_alert_policy"] = "terminal_failure_notify"
            terminal_failed += 1
        else:
            record["retry_allowed"] = True
            record["retry_after_at"] = (
                current + account2.timedelta(minutes=account2.RETRY_DELAY_MINUTES)
            ).isoformat()
            record["user_alert_policy"] = "deferred_transient_failure"
            deferred += 1

        merge_base = previous if same_revision else None
        events[token] = account2.primary_auto.merge_event_record(merge_base, record)

        join_outcomes.record_statistics(
            state,
            identifier=str(item.get("identifier") or key),
            account_key=config.account_key,
            status=str(events[token].get("status") or causal["status"]).casefold(),
            reason=reason,
            observed_at=current.isoformat(),
        )
        summary = join_outcomes.finalize_event_account_summary(state, item)
        outcomes.append(
            {
                "wheel_key": key,
                "identifier": str(item.get("identifier") or key),
                "status": str(events[token].get("status") or causal["status"]),
                "betboom_reason": reason,
                "retry_allowed": bool(events[token].get("retry_allowed")),
                "terminal_failure": str(events[token].get("status") or "").casefold()
                in join_outcomes.TERMINAL_FAILURE_STATUSES,
                "all_accounts_terminal_failure": bool(
                    summary.get("all_accounts_terminal_failure")
                ),
            }
        )

    state[config.last_run_state_field] = current.isoformat()
    monitor.save_state(state)
    return {
        "account_key": config.account_key,
        "account_label": config.label_getter(),
        "alert_user": config.alert_user_getter(),
        "auth_revision": auth_revision,
        "auth_rearmed": auth_rearmed,
        "attempted": attempted,
        "succeeded": succeeded,
        "participated_by_bot": clicked_successes,
        "already_participating_before_bot": preexisting,
        "terminal_failed": terminal_failed,
        "deferred": deferred,
        "skipped": skipped,
        "outcomes": outcomes,
    }


def run_account(
    name: str,
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    return run_configured_account(account_config(name), recovery_result_path)


def self_test() -> None:
    persistent.self_test()
    second = account_config("account2")
    third = account_config("account3")
    assert second.account_key == "vyacheslav_secondary"
    assert third.account_key == "xflarxx_primary"
    assert second.multi_account_version == 6
    item = {
        "wheel_key": "wheel",
        "action_id": 42,
        "server_start_at": "2026-07-22T12:00:00+00:00",
    }
    assert _account_event_token(second, item).endswith("#account:vyacheslav_secondary")
    assert _account_event_token(third, item).endswith("#account:xflarxx_primary")
    assert "ineligible_promo_code" in account2.TERMINAL_FAILURE_STATUSES
    assert _old_post_click_only_success(
        {"status": "participated", "confirmation_method": "betboom_post_click"}
    )
    assert not _old_post_click_only_success(
        {"status": "participated", "confirmation_method": "betboom_post_reload"}
    )
    current_revision = "auth:v1:current"
    old_success = {
        "status": "participated",
        "confirmation_method": "betboom_post_reload",
        "auth_revision": "auth:v1:old",
    }
    assert _should_attempt(old_success, monitor.now_utc(), current_revision)
    assert not _same_auth_revision(old_success, current_revision)
    same_success = dict(old_success, auth_revision=current_revision)
    assert not _should_attempt(same_success, monitor.now_utc(), current_revision)
    legacy_success = {
        "status": "participated",
        "confirmation_method": "betboom_post_reload",
    }
    assert _should_attempt(legacy_success, monitor.now_utc(), current_revision)

    class Result:
        detail = ""

    Result.status = "already_participating"
    fields = _causal_fields(Result())
    assert fields["status"] == "already_participating"
    assert fields["clicked_by_bot"] is False
    assert fields["participation_origin"] == "preexisting_verified"

    Result.status = "participated"
    Result.detail = persistent.PERSISTENCE_VERIFIED_MARKER
    fields = _causal_fields(Result())
    assert fields["status"] == "participated"
    assert fields["clicked_by_bot"] is True
    assert fields["participation_origin"] == "automatic"
    assert fields["confirmation_method"] == "betboom_post_reload"

    Result.detail = "clicked_by_bot=true"
    fields = _causal_fields(Result())
    assert fields["status"] == "unconfirmed"
    assert fields["confirmation_method"] == "betboom_post_click"

    Result.status = "ineligible_promo_code"
    Result.detail = (
        "BetBoom отказал в участии: Акция доступна только при регистрации по "
        "промокоду стримера; betboom_reason=promo_code_not_used; clicked_by_bot=true"
    )
    fields = _causal_fields(Result())
    assert fields["status"] == "ineligible_promo_code"
    assert fields["confirmation_method"] == "betboom_join_api"
    assert _betboom_reason(Result.detail) == "promo_code_not_used"
    print("shared secondary BetBoom participation runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, choices=("account2", "account3"))
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
