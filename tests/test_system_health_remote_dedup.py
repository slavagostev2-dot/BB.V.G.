from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/system-health.yml")


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_system_health_uses_existing_remote_delivery_claim() -> None:
    text = workflow_text()
    step = text.split("- name: Run centralized bot checks", 1)[1].split(
        "- name: Save incident state", 1
    )[0]

    assert "GITHUB_TOKEN: ${{ github.token }}" in step
    assert "GITHUB_REPOSITORY: ${{ github.repository }}" in step
    assert "GITHUB_BRANCH: main" in step


def test_system_health_keeps_one_serial_delivery_owner() -> None:
    text = workflow_text()
    concurrency = text.split("concurrency:", 1)[1].split("jobs:", 1)[0]

    assert "bb-vg-system-health" in concurrency
    assert "cancel-in-progress: false" in concurrency
