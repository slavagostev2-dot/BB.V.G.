from __future__ import annotations

from pathlib import Path

import runtime_state_publish
import xflarxx_runtime_integration

ROOT = Path(__file__).resolve().parents[1]
AUTO_WORKFLOW = ROOT / ".github" / "workflows" / "auto-participation.yml"
SITECUSTOMIZE = ROOT / "sitecustomize.py"


def test_large_runtime_state_uses_download_url() -> None:
    runtime_state_publish.self_test()


def test_auto_participation_setting_is_owner_only() -> None:
    owner_options = xflarxx_runtime_integration._notification_options_for_role("owner")
    user_options = xflarxx_runtime_integration._notification_options_for_role("user")
    admin_options = xflarxx_runtime_integration._notification_options_for_role("admin")
    assert any(item[0] == "auto_participation" for item in owner_options)
    assert not any(item[0] == "auto_participation" for item in user_options)
    assert not any(item[0] == "auto_participation" for item in admin_options)
    assert xflarxx_runtime_integration._is_owner({"owner_id": "1"}, "1")
    assert not xflarxx_runtime_integration._is_owner({"owner_id": "1"}, "2")


def test_workflow_has_no_direct_emergency_telegram_delivery() -> None:
    workflow = AUTO_WORKFLOW.read_text(encoding="utf-8")
    assert "--emergency-notify-event" not in workflow
    assert "Deliver owner-scoped emergency outcome" not in workflow
    assert workflow.count("python runtime_state_publish.py state.json") == 2
    assert "direct Telegram emergency delivery is disabled" in workflow


def test_runtime_state_behavior_is_not_hidden_in_sitecustomize() -> None:
    source = SITECUSTOMIZE.read_text(encoding="utf-8")
    assert "requests.sessions.Session.request" not in source
    assert "runtime_state_publish.py" in source
