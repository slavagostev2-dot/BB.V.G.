from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

import betboom_account_participation as account2
import betboom_auto_participation as auto
import betboom_participation_browser as browser
import betboom_profile_identity as profile_identity


PERSISTENCE_VERIFIED_MARKER = "server_persistence_verified=true"
SERVER_JOIN_ACK_MARKER = "server_join_acknowledged=true"
MAX_FRESH_VERIFICATION_ATTEMPTS = 3
FRESH_VERIFICATION_DELAY_SECONDS = 1.0


def session_fingerprint(storage_state: dict[str, Any] | None) -> str:
    """Return a non-secret digest used only to detect exact duplicate sessions."""

    if not isinstance(storage_state, dict):
        return ""
    canonical = json.dumps(
        storage_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_distinct_session(
    account_name: str,
    storage_state: dict[str, Any],
    *other_states: tuple[str, dict[str, Any] | None],
) -> None:
    current = session_fingerprint(storage_state)
    if not current:
        return
    for other_name, other_state in other_states:
        other = session_fingerprint(other_state)
        if other and other == current:
            raise RuntimeError(
                f"BetBoom session collision: {account_name} uses the exact same "
                f"storage_state as {other_name}"
            )


def _assert_resolved_profile_slot(storage_state: dict[str, Any]) -> None:
    """Reject a distinct storage blob that resolves to another configured profile."""

    second = account2.storage_state()
    if second is not None and storage_state == second:
        profile_identity.assert_account_slot_distinct(account2.ACCOUNT_KEY)
        return

    import xflarxx_account_participation as account3

    third = account3.storage_state()
    if third is not None and storage_state == third:
        profile_identity.assert_account_slot_distinct(account3.ACCOUNT_KEY)


def _participate_with_injected_storage(
    url: str,
    storage_state: dict[str, Any],
) -> auto.ParticipationResult:
    """Run the existing browser proof with an explicit account session."""

    result = browser.participate(url, storage_state=storage_state)
    return account2._reject_weak_browser_success(result)


def _server_acknowledged(result: auto.ParticipationResult) -> bool:
    return SERVER_JOIN_ACK_MARKER in str(result.detail or "").casefold()


def participate_with_persistence_proof(
    url: str,
    storage_state: dict[str, Any],
    *,
    participate_once: Callable[[str, dict[str, Any]], auto.ParticipationResult]
    | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> auto.ParticipationResult:
    """Click if needed, then verify the result in a fresh browser context.

    A terminal BetBoom join-API refusal is final immediately. A successful join
    response is authoritative for the click itself, but a fresh context still
    verifies account continuity. A fresh ``already_participating`` state is the
    strongest proof; a second authoritative successful join response is also
    accepted because it comes from a separate browser context and avoids a false
    failure when the BetBoom frontend has not refreshed its status yet.
    """

    if participate_once is None:
        _assert_resolved_profile_slot(storage_state)

    runner = participate_once or _participate_with_injected_storage
    initial = runner(url, storage_state)
    initial_status = str(initial.status or "").casefold()

    if not initial.success:
        return initial
    if initial_status == "already_participating":
        return initial
    if initial_status != "participated":
        return auto.ParticipationResult(
            False,
            "unconfirmed",
            f"unexpected_success_status:{initial.status}"[:300],
            initial.artifact_url,
        )

    initial_server_ack = _server_acknowledged(initial)
    last = initial
    for attempt in range(1, MAX_FRESH_VERIFICATION_ATTEMPTS + 1):
        if attempt > 1:
            sleep(FRESH_VERIFICATION_DELAY_SECONDS)
        verification = runner(url, storage_state)
        last = verification
        verification_status = str(verification.status or "").casefold()

        if verification.success and verification_status == "already_participating":
            detail = (
                "BetBoom подтвердил сохранённое участие в новом браузерном контексте; "
                f"{PERSISTENCE_VERIFIED_MARKER}; fresh_attempt={attempt}; "
                "initial_status=participated; verification_status=already_participating"
            )
            return auto.ParticipationResult(
                True,
                "participated",
                detail[:300],
                verification.artifact_url or initial.artifact_url,
            )

        # New API-first path: both independent browser contexts received an
        # authoritative join acknowledgement. Do not downgrade this to failure
        # merely because the SPA did not render its success label yet.
        if (
            initial_server_ack
            and verification.success
            and verification_status == "participated"
            and _server_acknowledged(verification)
        ):
            detail = (
                "BetBoom дважды подтвердил join API в независимых браузерных "
                f"контекстах; {PERSISTENCE_VERIFIED_MARKER}; "
                f"{SERVER_JOIN_ACK_MARKER}; fresh_attempt={attempt}; "
                "verification_method=join_api"
            )
            return auto.ParticipationResult(
                True,
                "participated",
                detail[:300],
                verification.artifact_url or initial.artifact_url,
            )

        if verification_status in account2.TERMINAL_FAILURE_STATUSES:
            return auto.ParticipationResult(
                False,
                verification_status,
                (
                    "После клика участие не подтвердилось при новой загрузке; "
                    f"fresh_attempt={attempt}; {verification.detail}"
                )[:300],
                verification.artifact_url or initial.artifact_url,
            )

    detail = (
        "BetBoom показывал успех после клика, но не подтвердил сохранённое участие "
        "в новом браузерном контексте; clicked_by_bot=true; "
        f"fresh_verification_attempts={MAX_FRESH_VERIFICATION_ATTEMPTS}; "
        f"last_status={last.status}"
    )
    return auto.ParticipationResult(
        False,
        "unconfirmed",
        detail[:300],
        last.artifact_url or initial.artifact_url,
    )


def self_test() -> None:
    profile_identity.self_test()
    configured = profile_identity.configured_sessions()
    if configured and all(storage is not None for _key, storage in configured):
        report = profile_identity.load_or_build_identity_report()
        assert isinstance(report.get("accounts"), dict)
        print(
            "BetBoom live profile identity preflight: "
            f"status={report.get('status')} "
            f"collision_groups={report.get('collision_groups', [])}"
        )

    state = {"cookies": [{"name": "session", "value": "a"}]}
    assert session_fingerprint(state) == session_fingerprint(dict(state))
    try:
        assert_distinct_session("account2", state, ("account1", dict(state)))
    except RuntimeError as exc:
        assert "session collision" in str(exc)
    else:
        raise AssertionError("exact duplicate BetBoom sessions must be rejected")

    captured: dict[str, Any] = {}
    original_browser = browser.participate
    browser.participate = lambda url, storage_state=None: (
        captured.update({"url": url, "storage_state": storage_state})
        or auto.ParticipationResult(False, "button_not_found", "test")
    )
    try:
        injected_result = _participate_with_injected_storage(
            "https://betboom.ru/freestream/injected",
            state,
        )
    finally:
        browser.participate = original_browser
    assert injected_result.status == "button_not_found"
    assert captured["storage_state"] is state

    sequence = iter(
        [
            auto.ParticipationResult(True, "participated", "optimistic", "a"),
            auto.ParticipationResult(
                True, "already_participating", "persisted", "b"
            ),
        ]
    )
    result = participate_with_persistence_proof(
        "https://betboom.ru/freestream/test",
        state,
        participate_once=lambda _url, _state: next(sequence),
        sleep=lambda _seconds: None,
    )
    assert result.success
    assert result.status == "participated"
    assert PERSISTENCE_VERIFIED_MARKER in result.detail
    assert result.artifact_url == "b"

    api_sequence = iter(
        [
            auto.ParticipationResult(
                True,
                "participated",
                f"accepted; {SERVER_JOIN_ACK_MARKER}",
                "api-a",
            ),
            auto.ParticipationResult(
                True,
                "participated",
                f"accepted again; {SERVER_JOIN_ACK_MARKER}",
                "api-b",
            ),
        ]
    )
    result = participate_with_persistence_proof(
        "https://betboom.ru/freestream/test",
        state,
        participate_once=lambda _url, _state: next(api_sequence),
        sleep=lambda _seconds: None,
    )
    assert result.success
    assert result.status == "participated"
    assert PERSISTENCE_VERIFIED_MARKER in result.detail
    assert SERVER_JOIN_ACK_MARKER in result.detail
    assert result.artifact_url == "api-b"

    optimistic_only = iter(
        [
            auto.ParticipationResult(True, "participated", "optimistic-0", "a0"),
            auto.ParticipationResult(True, "participated", "optimistic-1", "a1"),
            auto.ParticipationResult(True, "participated", "optimistic-2", "a2"),
            auto.ParticipationResult(True, "participated", "optimistic-3", "a3"),
        ]
    )
    result = participate_with_persistence_proof(
        "https://betboom.ru/freestream/test",
        state,
        participate_once=lambda _url, _state: next(optimistic_only),
        sleep=lambda _seconds: None,
    )
    assert not result.success
    assert result.status == "unconfirmed"

    preexisting = participate_with_persistence_proof(
        "https://betboom.ru/freestream/test",
        state,
        participate_once=lambda _url, _state: auto.ParticipationResult(
            True, "already_participating", "preexisting", "pre"
        ),
        sleep=lambda _seconds: None,
    )
    assert preexisting.success
    assert preexisting.status == "already_participating"

    terminal = participate_with_persistence_proof(
        "https://betboom.ru/freestream/test",
        state,
        participate_once=lambda _url, _state: auto.ParticipationResult(
            False,
            "ineligible_promo_code",
            "promo_code_not_used",
            "terminal",
        ),
        sleep=lambda _seconds: None,
    )
    assert not terminal.success
    assert terminal.status == "ineligible_promo_code"
    assert terminal.artifact_url == "terminal"

    print("BetBoom persisted participation proof self-test passed")


if __name__ == "__main__":
    self_test()
