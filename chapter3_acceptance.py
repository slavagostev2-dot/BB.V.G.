from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def self_test() -> None:
    workflow = text(".github/workflows/validate-current.yml")
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "contents: read" in workflow
    assert "continue-on-error" not in workflow
    assert "ref: main" not in workflow
    assert "git push" not in workflow
    assert "ci_verify_current_commit.py" in workflow
    assert "python -m pytest" in workflow
    assert "--cov-fail-under=80" in workflow
    assert "requirements-dev.txt" in workflow

    requirements = text("requirements-dev.txt")
    assert "pytest==" in requirements
    assert "pytest-cov==" in requirements

    tests = sorted((ROOT / "tests").glob("test_*.py"))
    assert len(tests) >= 4
    combined = "\n".join(path.read_text(encoding="utf-8") for path in tests)
    for required in (
        "test_full_detection_to_telegram_and_two_source_deduplication",
        "test_simultaneous_delivery_claim_sends_once",
        "test_reused_freestream_identifier_selects_current_event",
        "test_registration_and_personal_action_merge_without_data_loss",
        "test_remote_queue_retries_conflict_with_same_command",
        "test_wrong_checkout_is_rejected",
    ):
        assert required in combined

    router = text("notification_router.py")
    integrity = text("notification_integrity_v2.py")
    assert "def claim_delivery" in router
    assert "release_delivery(dedup_key)" in router
    assert "def claim_delivery" in integrity
    assert "def complete_delivery" in integrity
    assert not (ROOT / "current_validation_state.json").exists()
    print("Chapter 3 test-system acceptance contracts passed")


if __name__ == "__main__":
    self_test()
