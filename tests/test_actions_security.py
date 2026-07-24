from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import backup_rotation
import monitor_health

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

PINNED_ACTIONS = {
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
}
VALIDATION_WORKFLOWS = {
    "bot-recovery-smoke.yml",
    "telegram-resilience-check.yml",
    "current-checks.yml",
    "validate-current.yml",
    "validate-private-state.yml",
}
READ_ONLY_CHECKOUTS = VALIDATION_WORKFLOWS | {
    "admin-action.yml",
    "bot-state-backup.yml",
    "daily-report.yml",
    "telegram-domain-policy.yml",
    "monitor-watchdog.yml",
}
CONTENTS_WRITE_WORKFLOWS = {
    "telegram-source-transport.yml",
    "admin-action.yml",
    "admin-bot.yml",
    "auto-participation.yml",
    "bot-state-backup.yml",
    "monitor.yml",
    "nightly-discovery.yml",
    "rotate-bot-state-key.yml",
    "source-intelligence.yml",
    "source-registry.yml",
    "source-tier-maintenance.yml",
    "system-health.yml",
    "xflarxx-auto-participation.yml",
}
ACTIONS_WRITE_WORKFLOWS = {
    "admin-bot.yml",
    "monitor-watchdog.yml",
    "monitor.yml",
    "nightly-discovery.yml",
    "rotate-bot-state-key.yml",
    "system-health.yml",
    "validate-current.yml",
}
ACTION_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)


def workflow_texts() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
    }


def test_official_actions_are_immutably_pinned() -> None:
    for name, text in workflow_texts().items():
        for use in ACTION_RE.findall(text):
            action, separator, reference = use.partition("@")
            assert separator, f"Missing action reference in {name}: {use}"
            assert re.fullmatch(r"[0-9a-f]{40}", reference), (
                f"Moving or malformed action reference in {name}: {use}"
            )
            if action in PINNED_ACTIONS:
                assert reference == PINNED_ACTIONS[action], (
                    f"Unexpected provenance for {use} in {name}"
                )


def test_permissions_matrix_is_narrow() -> None:
    texts = workflow_texts()
    contents_write = {
        name for name, text in texts.items() if "contents: write" in text
    }
    actions_write = {
        name for name, text in texts.items() if "actions: write" in text
    }
    assert contents_write == CONTENTS_WRITE_WORKFLOWS
    assert actions_write == ACTIONS_WRITE_WORKFLOWS
    assert "contents: write" not in texts["telegram-domain-policy.yml"]
    assert "actions: write" not in texts["telegram-source-transport.yml"]
    for name, text in texts.items():
        assert not re.search(r"^  (?:contents|actions): write$", text, re.MULTILINE), (
            f"Write permission is workflow-wide in {name}"
        )


def test_validation_checks_out_exact_event_sha_without_credentials() -> None:
    texts = workflow_texts()
    exact_ref = "${{ github.event.pull_request.head.sha || github.sha }}"
    for name in VALIDATION_WORKFLOWS:
        text = texts[name]
        assert exact_ref in text, f"{name} does not checkout the exact event SHA"
        assert "persist-credentials: false" in text
    assert "--expected \"$EXPECTED_EVENT_SHA\"" in texts["validate-current.yml"]


def test_read_only_checkouts_do_not_persist_git_credentials() -> None:
    texts = workflow_texts()
    for name in READ_ONLY_CHECKOUTS:
        checkout_count = texts[name].count("actions/checkout@")
        assert checkout_count
        assert texts[name].count("persist-credentials: false") == checkout_count, name


def test_auto_participation_attempts_survive_an_unrelated_step_failure() -> None:
    text = workflow_texts()["auto-participation.yml"]
    guarded_condition = (
        "if: ${{ always() && steps.mode.outputs.probe != 'true' }}"
    )
    isolated_steps = (
        "Run event-based auto participation",
        "Retry current active wheels immediately",
        "Run second BetBoom account on fast result",
        "Run xFLARXx BetBoom account on fast result",
        "Recover fresh active wheels independently of monitor state",
        "Run second BetBoom account after full recovery",
        "Run xFLARXx BetBoom account after full recovery",
    )
    for step_name in isolated_steps:
        marker = f"- name: {step_name}"
        assert marker in text
        block = text.split(marker, 1)[1].split("\n      - name:", 1)[0]
        assert guarded_condition in block, (
            f"{step_name} can be skipped after another account or preflight failure"
        )


def test_backup_rotation_contract_and_concurrency() -> None:
    text = workflow_texts()["bot-state-backup.yml"]
    assert text.count("group: bb-vg-bot-state-backup") == 1
    assert "dry_run:" in text
    assert "python backup_rotation.py --self-test" in text
    assert "python backup_rotation.py" in text
    assert "CREATED_BACKUP_REF" in text
    assert backup_rotation.DEFAULT_KEEP_COUNT == 7
    assert 'KEEP_BACKUPS: "7"' in text
    assert "retain only the newest seven" in text
    backup_rotation.self_test()


def test_auto_participation_workflow_change_restarts_monitor() -> None:
    monitor_workflow = workflow_texts()["monitor.yml"]
    assert '- ".github/workflows/auto-participation.yml"' in monitor_workflow
    assert (
        "cancel-in-progress: ${{ github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.replace == true) }}"
        in monitor_workflow
    )


def test_production_heartbeat_contract_is_present() -> None:
    admin = workflow_texts()["admin-bot.yml"]
    health = (ROOT / "monitor_health.py").read_text(encoding="utf-8")
    for field in ("head_sha", "workflow_run_id", "run_attempt"):
        assert f'"{field}"' in admin
        assert f'"{field}"' in health


def test_control_center_runs_the_exact_validated_release() -> None:
    admin = workflow_texts()["admin-bot.yml"]
    validator = (ROOT / "scripts" / "validate_control_center.sh").read_text(
        encoding="utf-8"
    )
    assert "release_sha: ${{ steps.release.outputs.release_sha }}" in admin
    assert "ref: ${{ needs.validate-control-center.outputs.release_sha }}" in admin
    assert admin.count('      - "control_center_release.txt"') == 1
    assert "git checkout -B main origin/main" not in validator
    assert "git push origin HEAD:main" not in validator
    assert 'validation_stage="record_project_changelog"' not in validator


def test_cancelled_control_center_shift_never_self_dispatches() -> None:
    admin = workflow_texts()["admin-bot.yml"]
    replacement = admin.split(
        "      - name: Start replacement control-center shift", 1
    )[1]
    assert "steps.run_panel.outcome == 'success'" in replacement
    assert "steps.run_panel.outcome == 'cancelled'" not in replacement
    assert "Cancelled Control Center has no replacement" not in replacement
    assert replacement.count("gh workflow run admin-bot.yml") == 1


def test_control_center_starts_when_startup_heartbeat_hits_rate_limit() -> None:
    admin = workflow_texts()["admin-bot.yml"]
    startup = admin.split("      - name: Record panel status", 1)[1].split(
        "      - name: Run BB V.G. control center v41", 1
    )[0]
    assert "Control Center startup heartbeat was not published" in startup
    assert "exit 1" not in startup
    assert "exit 0" in startup
    assert 'ADMIN_CACHE_SECONDS: "60"' in admin


def test_failed_monitor_shift_is_recovered_only_by_watchdog() -> None:
    monitor = workflow_texts()["monitor.yml"]
    watchdog = workflow_texts()["monitor-watchdog.yml"]
    assert "restart_exit=1" in monitor
    assert 'exit "$restart_exit"' in monitor
    assert "steps.monitor_loop.outcome == 'success'" in monitor
    assert "steps.monitor_loop.outcome != 'cancelled'" not in monitor
    assert "gh workflow run monitor.yml --ref main -f continuous=true" in watchdog


def test_restart_recommendation_immediately_requests_controlled_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 7, 23, 19, 30, tzinfo=timezone.utc)
    status_path = tmp_path / "monitor_status.json"
    status_path.write_text(
        (
            '{"last_iteration_at":"2026-07-23T19:29:00+00:00",'
            '"last_successful_iteration_at":"2026-07-23T19:29:00+00:00",'
            '"consecutive_failures":1,"consecutive_no_progress":0,'
            '"restart_recommended":true}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor_health, "STATUS_PATH", status_path)
    monkeypatch.setattr(monitor_health, "now_utc", lambda: now)
    stale, reason, _ = monitor_health.health_check(
        max_age_minutes=30,
        max_success_age_minutes=30,
        max_consecutive_failures=2,
        max_consecutive_no_progress=2,
    )
    assert stale is True
    assert reason == "runtime requested controlled recovery"
