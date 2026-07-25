from pathlib import Path

import yaml


WORKFLOW_PATH = Path(".github/workflows/system-health.yml")


def test_system_health_uses_existing_remote_delivery_claim() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["health"]["steps"]
    run_checks = next(
        step for step in steps if step.get("name") == "Run centralized bot checks"
    )
    env = run_checks.get("env", {})

    assert env.get("GITHUB_TOKEN") == "${{ github.token }}"
    assert env.get("GITHUB_REPOSITORY") == "${{ github.repository }}"
    assert env.get("GITHUB_BRANCH") == "main"


def test_system_health_keeps_one_serial_delivery_owner() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    concurrency = workflow.get("concurrency", {})

    assert "bb-vg-system-health" in str(concurrency.get("group", ""))
    assert concurrency.get("cancel-in-progress") is False
