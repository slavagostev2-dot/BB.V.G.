from __future__ import annotations

from pathlib import Path

from admin_bot import Snapshot
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41
from scripts.telegram_start_state_smoke import run_smoke


ROLLBACK_RELEASE_SHA = "26efd716070d8933cb5aab0ceaef64d606236f21"


def test_telegram_start_and_state_smoke() -> None:
    run_smoke()


def test_release_validation_runs_telegram_start_state_smoke() -> None:
    validation = Path("scripts/validate_control_center.sh").read_text(encoding="utf-8")
    assert "python -m scripts.telegram_start_state_smoke" in validation
    assert "python scripts/telegram_start_state_smoke.py" not in validation
    assert "Release candidate is missing scripts/telegram_start_state_smoke.py" in validation


def test_only_emergency_rollback_release_may_precede_smoke_file() -> None:
    validation = Path("scripts/validate_control_center.sh").read_text(encoding="utf-8")
    assert f'elif [[ "$release_sha" == "{ROLLBACK_RELEASE_SHA}" ]]' in validation
    assert "grandfathered for the emergency rollback release" in validation
    assert validation.count(ROLLBACK_RELEASE_SHA) == 1


def test_snapshot_failures_cannot_be_replaced_with_empty_strings() -> None:
    source = Path("admin_panel_v2.py").read_text(encoding="utf-8")
    refresh = source.split("def refresh_snapshot", 1)[1].split("def snapshot", 1)[0]
    assert 'values[key] = ""' not in refresh
    assert "SnapshotUnavailableError" in refresh
    assert "return current" in refresh


def test_forced_snapshot_uses_local_cache_and_requests_background_refresh() -> None:
    panel = TelegramPanelRuntimeV41()
    cached = Snapshot(
        state={"active_wheels": {"wheel": {"identifier": "wheel"}}},
        stats={"daily": {}, "sources": {}},
        health={"sources": {}},
        discovery={},
        unknown={"samples": []},
        fast=["source"],
        nightly=[],
    )
    panel.snapshot_value = cached
    panel.snapshot_updated_at = 0.0
    panel.refresh_snapshot = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("request-path snapshot must not call GitHub")
    )

    assert panel.snapshot(force=True) is cached
    assert panel.refresh_requested.is_set()


def test_partial_snapshot_refresh_keeps_only_failed_component(
    monkeypatch,
) -> None:
    panel = TelegramPanelRuntimeV41()
    panel.snapshot_value = Snapshot(
        state={"active_wheels": {"old": {"identifier": "old"}}},
        stats={"daily": {}, "sources": {"old": {"checks": 1}}},
        health={"sources": {"verified": {"status": "ok"}}},
        discovery={"last_run_at": "old"},
        unknown={"samples": [{"value": "old"}]},
        fast=["old-source"],
        nightly=["old-reserve"],
    )
    values = {
        "state.json": '{"active_wheels":{"new":{"identifier":"new"}}}',
        "source_stats.json": '{"daily":{},"sources":{"new":{"checks":2}}}',
        "discovery_state.json": '{"last_run_at":"new"}',
        "unknown_timer_samples.json": '{"samples":[]}',
        "public_sources.txt": "new-source\n",
        "source_catalog.txt": "new-reserve\n",
    }
    calls: list[tuple[str, str]] = []

    def read(
        path: str,
        *,
        branch: str | None = None,
        revision: str | None = None,
    ) -> str:
        del revision
        calls.append((path, str(branch or "")))
        if path == "source_health.json":
            raise RuntimeError("simulated GitHub 403")
        return values[path]

    monkeypatch.setenv("BBVG_RUNTIME_STATE_BRANCH", "runtime-state")
    panel._branch_head_sha = lambda branch: f"{branch:0<40}"[:40]  # type: ignore[method-assign]
    panel._direct_get_file = read  # type: ignore[method-assign]

    refreshed = panel.refresh_snapshot()

    assert set(refreshed.state["active_wheels"]) == {"new"}
    assert refreshed.stats["sources"]["new"]["checks"] == 2
    assert refreshed.health == {"sources": {"verified": {"status": "ok"}}}
    assert refreshed.discovery["last_run_at"] == "new"
    assert refreshed.fast == ["new-source"]
    assert refreshed.nightly == ["new-reserve"]
    assert ("state.json", "runtime-state") in calls
    assert ("source_stats.json", "runtime-state") in calls
    assert ("public_sources.txt", "main") in calls


def test_snapshot_refresh_uses_one_api_ref_lookup_per_branch(
    monkeypatch,
) -> None:
    panel = TelegramPanelRuntimeV41()
    values = {
        "state.json": '{"active_wheels":{}}',
        "source_stats.json": '{"daily":{},"sources":{}}',
        "source_health.json": '{"sources":{}}',
        "discovery_state.json": "{}",
        "unknown_timer_samples.json": '{"samples":[]}',
        "public_sources.txt": "source\n",
        "source_catalog.txt": "reserve\n",
    }
    resolved: list[str] = []
    revisions: list[tuple[str, str, str]] = []

    def resolve(branch: str) -> str:
        resolved.append(branch)
        return ("a" if branch == "runtime-state" else "b") * 40

    def read(
        path: str,
        *,
        branch: str | None = None,
        revision: str | None = None,
    ) -> str:
        revisions.append((path, str(branch or ""), str(revision or "")))
        return values[path]

    monkeypatch.setenv("BBVG_RUNTIME_STATE_BRANCH", "runtime-state")
    panel._branch_head_sha = resolve  # type: ignore[method-assign]
    panel._direct_get_file = read  # type: ignore[method-assign]

    panel.refresh_snapshot()

    assert resolved == ["main", "runtime-state"]
    assert len(revisions) == 7
    assert {
        revision for _path, branch, revision in revisions if branch == "runtime-state"
    } == {"a" * 40}
    assert {
        revision for _path, branch, revision in revisions if branch == "main"
    } == {"b" * 40}
