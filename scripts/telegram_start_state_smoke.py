from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from admin_bot import Snapshot
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41
from admin_panel_v2 import SnapshotUnavailableError


def _message() -> dict[str, Any]:
    return {
        "message_id": 1,
        "text": "/start",
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "first_name": "Owner"},
    }


def _set_owner_context(panel: TelegramPanelRuntimeV41) -> None:
    def set_context(chat_id: Any, user_id: Any) -> None:
        panel.current_chat_id = str(chat_id)
        panel.current_user_id = str(user_id)
        panel.current_role = "owner"

    panel.set_context = set_context  # type: ignore[method-assign]


def verify_start_success() -> None:
    panel = TelegramPanelRuntimeV41()
    _set_owner_context(panel)
    calls: list[tuple[str, Any]] = []
    panel.register_user = lambda message: "owner"  # type: ignore[method-assign]
    panel.can_view = lambda: True  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel.handle_message(_message())
    assert calls == [("menu", True)]


def verify_start_failure_is_visible() -> None:
    panel = TelegramPanelRuntimeV41()
    _set_owner_context(panel)
    sent: list[str] = []

    def fail_registration(message: dict[str, Any]) -> str:
        raise RuntimeError("simulated access read failure")

    panel.register_user = fail_registration  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: sent.append(str(text)) or {}  # type: ignore[method-assign]
    panel.handle_message(_message())
    assert sent
    assert "данные не обнулены" in sent[-1]


def verify_failed_refresh_keeps_verified_snapshot() -> None:
    panel = TelegramPanelRuntimeV41()
    existing = Snapshot(
        state={"active_wheels": {"wheel": {"identifier": "wheel"}}},
        stats={"daily": {"2026-07-25": {"totals": {"checks": 17}}}, "sources": {}},
        health={"sources": {"source": {"status": "ok"}}},
        discovery={},
        unknown={"samples": []},
        fast=["source"],
        nightly=[],
    )
    panel.snapshot_value = existing

    def fail_read(
        path: str,
        *,
        branch: str | None = None,
        revision: str | None = None,
    ) -> str:
        del branch, revision
        raise RuntimeError(f"simulated read failure: {path}")

    panel._branch_head_sha = lambda _branch: "a" * 40  # type: ignore[method-assign]
    panel._direct_get_file = fail_read  # type: ignore[method-assign]
    refreshed = panel.refresh_snapshot()
    assert refreshed is existing
    assert len(refreshed.fast) == 1
    assert len(refreshed.state["active_wheels"]) == 1


def verify_initial_failure_is_not_zero_state() -> None:
    panel = TelegramPanelRuntimeV41()

    def fail_read(
        path: str,
        *,
        branch: str | None = None,
        revision: str | None = None,
    ) -> str:
        del branch, revision
        raise RuntimeError(f"simulated read failure: {path}")

    panel._branch_head_sha = lambda _branch: "a" * 40  # type: ignore[method-assign]
    panel._direct_get_file = fail_read  # type: ignore[method-assign]
    try:
        panel.refresh_snapshot()
    except SnapshotUnavailableError:
        return
    raise AssertionError("initial critical read failure must not become an all-zero snapshot")


def verify_populated_snapshot_remains_populated() -> None:
    panel = TelegramPanelRuntimeV41()
    values = {
        "state.json": json.dumps({"active_wheels": {"wheel": {"identifier": "wheel"}}}),
        "source_stats.json": json.dumps({"sources": {"source": {"checks": 5}}, "daily": {}}),
        "source_health.json": json.dumps({"sources": {"source": {"status": "ok"}}}),
        "discovery_state.json": "{}",
        "unknown_timer_samples.json": json.dumps({"samples": []}),
        "public_sources.txt": "source\n",
        "source_catalog.txt": "reserve\n",
    }
    panel._branch_head_sha = lambda _branch: "a" * 40  # type: ignore[method-assign]
    panel._direct_get_file = (  # type: ignore[method-assign]
        lambda path, *, branch=None, revision=None: values[path]
    )
    snap = panel.refresh_snapshot()
    assert len(snap.fast) == 1
    assert len(snap.nightly) == 1
    assert len(snap.state["active_wheels"]) == 1
    assert snap.stats["sources"]["source"]["checks"] == 5


def run_smoke() -> None:
    verify_start_success()
    verify_start_failure_is_visible()
    verify_failed_refresh_keeps_verified_snapshot()
    verify_initial_failure_is_not_zero_state()
    verify_populated_snapshot_remains_populated()
    print("Telegram /start and non-zero state safety smoke passed")


if __name__ == "__main__":
    run_smoke()
