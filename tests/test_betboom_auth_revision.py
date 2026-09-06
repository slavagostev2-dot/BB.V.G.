from __future__ import annotations

from datetime import datetime, timezone

import betboom_auto_participation as auto
import betboom_participation_browser as browser
import betboom_persistent_participation as persistent
import betboom_profile_identity as identity
import secondary_account_participation as secondary


UTC = timezone.utc


def test_auth_revision_tracks_profile_not_cookie_snapshot() -> None:
    profile = "0123456789abcdef01234567"
    first = identity.auth_revision_from_profile_fingerprint(profile)
    refreshed_cookie_same_profile = identity.auth_revision_from_profile_fingerprint(profile)
    another_profile = identity.auth_revision_from_profile_fingerprint(
        "fedcba9876543210fedcba98"
    )

    assert first.startswith("auth:v1:")
    assert first == refreshed_cookie_same_profile
    assert first != another_profile
    assert profile not in first


def test_secondary_success_from_another_auth_revision_is_rechecked() -> None:
    previous = {
        "status": "participated",
        "confirmation_method": "betboom_post_reload",
        "auth_revision": "auth:v1:old-profile",
    }

    assert secondary._should_attempt(
        previous,
        datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        "auth:v1:new-profile",
    )
    assert not secondary._same_auth_revision(previous, "auth:v1:new-profile")


def test_secondary_success_same_auth_revision_remains_deduplicated() -> None:
    previous = {
        "status": "participated",
        "confirmation_method": "betboom_post_reload",
        "auth_revision": "auth:v1:same-profile",
    }

    assert not secondary._should_attempt(
        previous,
        datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        "auth:v1:same-profile",
    )


def test_injected_storage_reaches_browser_without_global_session_lookup(monkeypatch) -> None:
    injected = {"cookies": [{"name": "session", "value": "account-two"}]}
    captured: dict[str, object] = {}

    def fake_participate(url: str, storage_state=None):
        captured["url"] = url
        captured["storage_state"] = storage_state
        return auto.ParticipationResult(False, "button_not_found", "test")

    monkeypatch.setattr(browser, "participate", fake_participate)
    monkeypatch.setattr(
        auto,
        "_storage_state",
        lambda: (_ for _ in ()).throw(
            AssertionError("global primary session lookup was used")
        ),
    )

    result = persistent._participate_with_injected_storage(
        "https://betboom.ru/freestream/example",
        injected,
    )

    assert result.status == "button_not_found"
    assert captured["storage_state"] is injected


def test_mismatched_old_success_cannot_win_new_profile_merge() -> None:
    old_success = {
        "status": "participated",
        "auth_revision": "auth:v1:old-profile",
        "attempted_at": "2026-09-06T10:00:00+00:00",
    }
    current_failure = {
        "status": "button_not_found",
        "auth_revision": "auth:v1:new-profile",
        "attempted_at": "2026-09-06T11:00:00+00:00",
    }

    # Different revisions deliberately use no merge base. This is the exact
    # rule used by the shared secondary runner after a profile replacement.
    merge_base = (
        old_success
        if secondary._same_auth_revision(old_success, current_failure["auth_revision"])
        else None
    )
    merged = auto.merge_event_record(merge_base, current_failure)

    assert merged["status"] == "button_not_found"
    assert merged["auth_revision"] == "auth:v1:new-profile"
