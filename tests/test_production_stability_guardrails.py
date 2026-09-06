from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_wheel_voting import PersonalWheelVotingMixin


ROOT = Path(__file__).resolve().parents[1]
MONITOR_ENTRYPOINT = ROOT / "bbvg_monitor_main.py"
CONTROL_CENTER_ENTRYPOINT = ROOT / "notification_button_recovery.py"
POLICY_PATH = ROOT / "engineering" / "PRODUCTION_STABILITY_POLICY_RU.md"


EXPECTED_MONITOR_PATCH_TARGETS = {
    "notification_router.load_config",
    "monitor.is_suppressed.__module__",
    "monitor.is_activation_suppressed.__module__",
    "monitor.all_failed_alert_due",
    "monitor.automatic_status_due",
    "monitor.BOT_FEEDBACK_ENABLED",
    "monitor.process_admin_actions",
    "runtime.base_runtime._recover_deadline",
    "monitor.data_store.load_stats",
    "monitor.data_store.record_admin_wheel_decision",
    "monitor.data_store.save_stats",
    "monitor.wheel_reply_markup",
    "monitor.process_active_wheels",
    "monitor.send_message",
    "personal_reminder_filter._recoverable_processed_failure",
}

EXPECTED_MONITOR_INSTALL_ORDER = (
    "notification_preferences_v2.install",
    "personal_wheel_voting.install_notification_router",
    "recurring_wheel_events.install",
    "telegram_transport.install",
    "telegram_post_links_v2.install",
    "telegram_private_sources.install",
    "wheel_event_runtime.install",
    "wheel_metadata_quality.install",
    "wheel_publications_v2.install",
    "restart_duplicate_guard.install",
    "wheel_link_lifecycle.install",
    "notification_navigation.install",
    "wheel_lifecycle_v2.install",
    "personal_reminder_filter.install",
    "vk_wheel_notifications.install",
)

EXPECTED_CONTROL_CENTER_INSTALL_ORDER = (
    "_install_fast_outcome_policy",
    "wheel_detection_reliability.install_owner_notification_update",
    "auto_participation_notifications.install",
    "auto_participation_backlog_guard.install",
    "xflarxx_account_participation.install_owner_sync",
    "_install_auto_outcome_delivery_claims",
    "xflarxx_runtime_integration.install",
)

EXPECTED_CONTROL_CENTER_PATCH_TARGETS = {
    "owner_sync.SYNC_INTERVAL_SECONDS",
    "admin_panel_v2.CACHE_REFRESH_SECONDS",
    "owner_sync._bbvg_fast_outcome_policy_installed",
    "panel.send",
    "auto_participation_notifications._result_message",
    "xflarxx_account_participation._message",
    "auto_participation_notifications.sync_once",
    "owner_sync.sync_once",
    "owner_sync._bbvg_auto_outcome_delivery_claims_installed",
}


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    if isinstance(node, ast.AugAssign):
        return [node.target]
    return []


def _module_level_attribute_assignments(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for statement in tree.body:
        for target in _assignment_targets(statement):
            name = _dotted_name(target)
            if name and "." in name:
                result.add(name)
    return result


def _production_attribute_assignments(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        for target in _assignment_targets(node):
            name = _dotted_name(target)
            if name.startswith(
                (
                    "owner_sync.",
                    "admin_panel_v2.",
                    "auto_participation_notifications.",
                    "xflarxx_account_participation.",
                    "panel.send",
                )
            ):
                result.add(name)
    return result


def _top_level_install_calls(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        name = _dotted_name(statement.value.func)
        if "install" in name:
            result.append(name)
    return tuple(result)


def test_monitor_runtime_patch_surface_is_frozen() -> None:
    actual = _module_level_attribute_assignments(MONITOR_ENTRYPOINT)
    relevant = {
        name
        for name in actual
        if name.startswith(
            ("monitor.", "runtime.", "notification_router.", "personal_reminder_filter.")
        )
    }
    assert relevant == EXPECTED_MONITOR_PATCH_TARGETS
    assert _top_level_install_calls(MONITOR_ENTRYPOINT) == EXPECTED_MONITOR_INSTALL_ORDER


def test_control_center_runtime_patch_surface_is_frozen() -> None:
    assert _production_attribute_assignments(CONTROL_CENTER_ENTRYPOINT) == (
        EXPECTED_CONTROL_CENTER_PATCH_TARGETS
    )
    assert _top_level_install_calls(CONTROL_CENTER_ENTRYPOINT) == (
        EXPECTED_CONTROL_CENTER_INSTALL_ORDER
    )


def test_wheel_participation_callbacks_have_one_subject_owner() -> None:
    owner = (ROOT / "personal_wheel_voting.py").read_text(encoding="utf-8")
    runtime_v41 = (ROOT / "admin_panel_runtime_v41.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "notification_button_recovery.py").read_text(
        encoding="utf-8"
    )
    assert "def _notification_wheel_key" in owner
    assert 'if data.startswith(("bb:p:", "wheel:part:"))' in owner
    assert "def _mark_personal_from_notification" not in runtime_v41
    assert 'if data.startswith("bb:p:")' not in runtime_v41
    assert 'if data.startswith("wheel:part:")' not in runtime_v41
    assert "def _mark_personal_from_notification" not in entrypoint
    assert "def _notification_token" not in entrypoint
    runtime = (ROOT / "bbvg/bot/runtime.py").read_text(encoding="utf-8")
    assert 'data.startswith(("bb:p:", "wheel:part:"))' not in runtime
    assert 'data.startswith("bb:p:")' not in runtime
    assert 'data.startswith("wheel:part:")' not in runtime


def test_notification_callback_resolver_uses_saved_context_and_active_fallback() -> None:
    panel = PersonalWheelVotingMixin.__new__(PersonalWheelVotingMixin)
    panel.snapshot = lambda force=True: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {"saved": {"wheel_key": "Wheel-B"}}}
    )
    assert panel._notification_wheel_key("saved") == "wheel-b"

    active = {
        "source": "hoochcs2",
        "message_id": 2198,
        "identifier": "hooch07",
    }
    token = panel._notification_token("hooch07", active)
    assert token == "cba7abb40c5b77"
    panel.snapshot = lambda force=True: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {}, "active_wheels": {"hooch07": active}}
    )
    assert panel._notification_wheel_key(token) == "hooch07"

    panel.snapshot = lambda force=True: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {}, "active_wheels": {}}
    )
    with pytest.raises(ValueError, match="Контекст кнопки устарел"):
        panel._notification_wheel_key("missing")


def test_single_callback_owner_preserves_notification_and_active_list_behavior() -> None:
    panel = PersonalWheelVotingMixin.__new__(PersonalWheelVotingMixin)
    events: list[tuple[str, object]] = []
    panel._edit_message_id = None
    panel._prepare_callback_user = lambda query: events.append(  # type: ignore[method-assign]
        ("prepare", str(query.get("data") or ""))
    )
    panel.snapshot = lambda force=True: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {"saved": {"wheel_key": "wheel-b"}}}
    )
    panel._resolve_wheel_token = lambda token: token  # type: ignore[method-assign]
    panel.mark_personal_participation = lambda key: (  # type: ignore[method-assign]
        events.append(("participate", key)),
        {"changed": True},
    )[1]
    panel.answer = lambda query_id, text: events.append(("answer", text))  # type: ignore[method-assign]
    panel._delete_callback_message = lambda query: events.append(  # type: ignore[method-assign]
        ("delete", str(query.get("data") or ""))
    )
    panel.show_menu = lambda clear_stack=True: events.append(("menu", clear_stack))  # type: ignore[method-assign]

    panel.handle_callback(
        {
            "id": "q-notification",
            "data": "bb:p:saved",
            "message": {"message_id": 77},
        }
    )
    assert ("participate", "wheel-b") in events
    assert ("delete", "bb:p:saved") in events
    assert not any(name == "menu" for name, _ in events)
    assert panel._edit_message_id is None

    events.clear()
    panel.handle_callback(
        {
            "id": "q-active",
            "data": "wheel:part:wheel-a",
            "message": {"message_id": 78},
        }
    )
    assert ("participate", "wheel-a") in events
    assert ("menu", True) in events
    assert not any(name == "delete" for name, _ in events)
    assert panel._edit_message_id is None


def test_historical_panel_runtime_ladder_cannot_return() -> None:
    versioned = sorted(path.name for path in ROOT.glob("admin_panel_runtime_v*.py"))
    assert versioned == ["admin_panel_runtime_v41.py"]


def test_stability_policy_covers_the_whole_user_path() -> None:
    policy = POLICY_PATH.read_text(encoding="utf-8")
    for marker in (
        "Кнопки и callback",
        "Поиск и классификация",
        "Уведомления и дедупликация",
        "Автоучастие",
        "Синхронизация состояния",
        "Живой production",
        "Новые runtime-подмены запрещены",
        "Автоматический откат",
    ):
        assert marker in policy


def test_repository_instructions_forbid_new_patch_layers() -> None:
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for marker in (
        "Запрет новых runtime-заплаток",
        "module.function = wrapper",
        "новый `install()`",
        "PRODUCTION_STABILITY_POLICY_RU.md",
        "test_production_stability_guardrails.py",
    ):
        assert marker in instructions


def test_current_checks_run_guardrails_before_full_release_validation() -> None:
    workflow = (ROOT / ".github/workflows/current-checks.yml").read_text(
        encoding="utf-8"
    )
    guard = "tests/test_production_stability_guardrails.py"
    acceptance = "tests/production_acceptance.py --section stability"
    full_pytest = "python -m pytest -q"
    assert guard in workflow
    assert acceptance in workflow
    assert full_pytest in workflow
    assert workflow.index(guard) < workflow.index(acceptance) < workflow.rindex(
        full_pytest
    )


def test_auto_participation_keeps_all_independent_account_stages() -> None:
    workflow = (ROOT / ".github/workflows/auto-participation.yml").read_text(
        encoding="utf-8"
    )
    markers = (
        "Run event-based auto participation",
        "Retry current active wheels immediately",
        "Run second BetBoom account on fast result",
        "Run xFLARXx BetBoom account on fast result",
        "Recover fresh active wheels independently of monitor state",
        "Run second BetBoom account after full recovery",
        "Run xFLARXx BetBoom account after full recovery",
        "Persist participation state without losing concurrent monitor updates",
    )
    positions = [workflow.index(marker) for marker in markers]
    assert positions == sorted(positions)
