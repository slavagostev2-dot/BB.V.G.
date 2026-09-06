from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

import betboom_account_participation as account2
import betboom_auto_participation as auto
import betboom_profile_identity as profile_identity


PERSISTENCE_VERIFIED_MARKER = "server_persistence_verified=true"
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

    # Import lazily to avoid widening the module dependency graph during unit
    # self-tests and primary-account startup.
    import xflarxx_account_participation as account3

    third = account3.storage_state()
    if third is not None and storage_state == third:
        profile_identity.assert_account_slot_distinct(account3.ACCOUNT_KEY)


def participate_with_persistence_proof(
    url: str,
    storage_state: dict[str, Any],
    *,
    participate_once: Callable[[str, dict[str, Any]], auto.ParticipationResult]
    | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> auto.ParticipationResult:
    """Click if needed, then require a fresh-page persisted account state.

    BetBoom may optimistically render the success label immediately after a click.
    That UI transition is not authoritative. A successful automatic result is only
    returned after a separate browser context, created from the saved session,
    opens the same wheel and observes participation *before* any new click.

    Production calls also resolve the authenticated BetBoom profile behind the
    storage state. Two different cookie blobs are not allowed to masquerade as
    separate account slots when BetBoom resolves them to the same profile.
    """

    if participate_once is None:
        _assert_resolved_profile_slot(storage_state)

    runner = participate_once or account2._participate_with_storage
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
    state = {"cookies": [{"name": "session", "value": "a"}]}
    assert session_fingerprint(state) == session_fingerprint(dict(state))
    try:
        assert_distinct_session("account2", state, ("account1", dict(state)))
    except RuntimeError as exc:
        assert "session collision" in str(exc)
    else:
        raise AssertionError("exact duplicate BetBoom sessions must be rejected")

    sequence = iter(
        [
            auto.ParticipationResult(True, "participated", "optimistic", "a"),
            auto.ParticipationResult(True, "already_participating", "persisted", "b"),
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
    assert preexisting.success and preexisting.status == "already_participating"

    print("BetBoom persisted participation proof self-test passed")


if __name__ == "__main__":
    self_test()
