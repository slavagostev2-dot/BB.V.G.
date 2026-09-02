from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import system_checks


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "system-health.yml"


def test_system_health_loads_authoritative_runtime_snapshots_before_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Load authoritative runtime snapshots" in workflow
    assert "load_snapshot runtime-status" in workflow
    assert "load_snapshot runtime-state" in workflow
    for file_name in ("state.json", "source_stats.json", "source_health.json"):
        assert file_name in workflow

    load_position = workflow.index("Load authoritative runtime snapshots")
    checks_position = workflow.index("Run centralized bot checks")
    assert load_position < checks_position


def test_system_health_does_not_diagnose_from_stale_main_source_health() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    runtime_state_loop = (
        "for file in state.json source_stats.json source_health.json; do\n"
        "            load_snapshot runtime-state \"$file\""
    )
    assert runtime_state_loop in workflow
    assert "Authoritative runtime-status and runtime-state snapshots loaded" in workflow


def test_system_health_cleans_diagnostic_snapshots_before_rebase_retry() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    commit_position = workflow.index(
        'git commit -m "Update BB V.G. incident state [skip ci]"'
    )
    restore_position = workflow.index("git restore --worktree -- .")
    rebase_position = workflow.index("git pull --rebase origin main")
    assert commit_position < restore_position < rebase_position


def test_optional_ai_provider_cannot_block_deterministic_health() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "deterministic health diagnostics" in workflow
    assert 'assert config.provider in {"gemini", "openai"}' not in workflow
    assert "assert config.provider_configured()" not in workflow


def test_health_matches_single_public_source_model() -> None:
    active_domain_files = {path.name for path in system_checks.ACTIVE_DOMAIN_FILES}
    assert "nightly_discovery.py" not in active_domain_files
    assert not hasattr(system_checks, "SOURCE_TIER_STATE_PATH")


def test_transport_verification_has_a_recurring_schedule() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "telegram-source-transport.yml"
    ).read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert 'cron: "17 3 * * *"' in workflow


def test_source_workflows_do_not_require_removed_secondary_inventory() -> None:
    domain_workflow = (
        ROOT / ".github" / "workflows" / "telegram-domain-policy.yml"
    ).read_text(encoding="utf-8")
    transport_workflow = (
        ROOT / ".github" / "workflows" / "telegram-source-transport.yml"
    ).read_text(encoding="utf-8")
    assert 'read_sources("source_catalog.txt")' not in domain_workflow
    assert '      - "source_catalog.txt"' not in transport_workflow


def test_health_reads_current_intelligence_without_removed_discovery_state() -> None:
    fixed = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    with TemporaryDirectory() as temporary:
        intelligence = Path(temporary) / "intelligence_state.json"
        intelligence.write_text(
            json.dumps({
                "telegram_domain": "telegram.me",
                "last_run_at": "2026-09-01T21:08:34+00:00",
                "last_run_summary": {
                    "known_sources": 172,
                    "sources_scanned": 172,
                    "errors": 0,
                },
            }),
            encoding="utf-8",
        )
        original_path = system_checks.INTELLIGENCE_PATH
        original_now = system_checks.now_utc
        try:
            system_checks.INTELLIGENCE_PATH = intelligence
            system_checks.now_utc = lambda: fixed
            details: dict = {}
            findings: list[dict] = []
            system_checks.check_discovery_runtime(details, findings)
        finally:
            system_checks.INTELLIGENCE_PATH = original_path
            system_checks.now_utc = original_now

    assert not findings
    assert details["discovery"]["intelligence_domain"] == "telegram.me"
    assert "domain" not in details["discovery"]


def test_quarantine_has_one_incident_per_source_without_summary_duplicate() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        public = root / "public_sources.txt"
        nightly = root / "source_catalog.txt"
        health = root / "source_health.json"
        public.write_text("alpha\n", encoding="utf-8")
        nightly.write_text("", encoding="utf-8")
        health.write_text(json.dumps({
            "sources": {
                "alpha": {
                    "status": "quarantined",
                    "failure_code": "empty_public_feed",
                    "failure_reason": "публичная страница открылась, но сообщений не найдено",
                }
            }
        }), encoding="utf-8")
        original = (
            system_checks.PUBLIC_SOURCES_PATH,
            system_checks.NIGHTLY_SOURCES_PATH,
            system_checks.HEALTH_PATH,
        )
        try:
            system_checks.PUBLIC_SOURCES_PATH = public
            system_checks.NIGHTLY_SOURCES_PATH = nightly
            system_checks.HEALTH_PATH = health
            findings: list[dict] = []
            system_checks.check_source_health({}, findings)
        finally:
            (
                system_checks.PUBLIC_SOURCES_PATH,
                system_checks.NIGHTLY_SOURCES_PATH,
                system_checks.HEALTH_PATH,
            ) = original

    assert [item["kind"] for item in findings] == ["source_empty_public_feed"]
