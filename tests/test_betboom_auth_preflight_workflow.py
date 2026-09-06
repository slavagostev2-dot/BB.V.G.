from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/betboom-auth-preflight.yml")


def test_betboom_auth_preflight_is_manual_and_non_participating() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "schedule:" not in text
    assert "persist-credentials: false" in text

    assert "betboom_profile_identity" in text
    assert "identity.build_identity_report()" in text
    assert "betboom_session_health" in text
    assert "session_health.run()" in text

    # This workflow is deliberately identity/session-only. It must never open a
    # wheel or call any participation runner.
    assert "/freestream/" not in text
    assert "auto_participation_worker" not in text
    assert "secondary_account_participation" not in text
    assert "participate(" not in text
    assert "participate_with_persistence_proof" not in text


def test_betboom_auth_preflight_requires_all_three_secret_pairs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for part in range(1, 7):
        name = f"BETBOOM_STORAGE_STATE_JSON_PART{part}"
        assert name in text
        assert f"secrets.{name}" in text


def test_betboom_auth_preflight_fails_closed_on_identity_or_session_problem() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'if status != "ok"' in text
    assert "if collision_groups:" in text
    assert 'if len(set(revisions)) != len(revisions):' in text
    assert 'if row.get("session_active") is not True:' in text
