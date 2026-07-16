from __future__ import annotations

import compileall
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_script(path: str, *args: str) -> None:
    subprocess.run([sys.executable, path, *args], cwd=ROOT, check=True)


def main() -> int:
    os.environ.setdefault("BOT_TOKEN", "test-bot-token")
    os.environ.setdefault("BOT_STATE_KEY", "test-state-key")
    os.environ.setdefault("BOT_CHAT_ID", "1")
    os.environ.setdefault("TELEGRAM_WEB_DOMAIN", "telegram.me")
    os.environ.setdefault("EXPECTED_SOURCE_COUNT", "66")
    os.environ.setdefault("FINAL_REMINDER_BEFORE_MINUTES", "5")
    os.environ.setdefault("ACTIVE_REMOVE_GRACE_MINUTES", "0")
    os.environ["UNKNOWN_DEDUP_HOURS"] = "2"

    files = (
        "monitor.py",
        "monitor_entry.py",
        "bbvg_monitor_runtime.py",
        "bbvg_monitor_main.py",
        "telegram_transport.py",
        "telegram_post_links_v2.py",
        "recurring_wheel_events.py",
        "restart_duplicate_guard.py",
        "wheel_event_runtime.py",
        "wheel_link_lifecycle.py",
        "wheel_scenario_suite.py",
        "wheel_metadata_quality.py",
        "wheel_publications_v2.py",
        "wheel_lifecycle_v2.py",
        "admin_action.py",
        "admin_action_v2.py",
        "admin_action_v3.py",
        "admin_action_queue.py",
        "chapter1_stability.py",
        "chapter5_acceptance.py",
        "monitor_health.py",
        "incident_manager.py",
        "system_checks.py",
        "notification_router.py",
        "notification_integrity_v2.py",
        "bot_notification_state.py",
        "bot_private_state.py",
        "privacy_retention.py",
        "monitor_data.py",
    )
    for path in files:
        if not compileall.compile_file(ROOT / path, quiet=1, force=True):
            raise RuntimeError(f"Compilation failed: {path}")

    for path, args in (
        ("notification_router.py", ()),
        ("notification_integrity_v2.py", ("--self-test",)),
        ("admin_action_v2.py", ("--self-test",)),
        ("admin_action_v3.py", ("--self-test",)),
        ("admin_action_queue.py", ()),
        ("telegram_transport.py", ()),
        ("telegram_post_links_v2.py", ()),
        ("recurring_wheel_events.py", ()),
        ("restart_duplicate_guard.py", ()),
        ("wheel_event_runtime.py", ()),
        ("wheel_link_lifecycle.py", ()),
        ("wheel_scenario_suite.py", ()),
        ("wheel_metadata_quality.py", ()),
        ("wheel_publications_v2.py", ()),
        ("wheel_lifecycle_v2.py", ()),
        ("chapter5_acceptance.py", ()),
        ("monitor_health.py", ("--self-test",)),
        ("incident_manager.py", ()),
        ("system_checks.py", ("--self-test",)),
        ("chapter1_stability.py", ()),
    ):
        run_script(path, *args)

    import bbvg_monitor_main as runtime
    import monitor
    import monitor_data
    import notification_router

    assert runtime.monitor.remember_pending.__module__ == "wheel_metadata_quality"
    assert runtime.monitor.wheel_reply_markup.__module__ == "bbvg_monitor_main"
    assert runtime.monitor.process_active_wheels.__module__ == "wheel_lifecycle_v2"
    assert runtime.runtime.base_runtime._persist_publications.__module__ == "wheel_publications_v2"
    # The public integration name stays compatible with the original workflow;
    # the installation flag below proves the timer-aware lifecycle is active.
    assert runtime.monitor.is_suppressed.__module__ == "wheel_publications_v2"
    assert runtime.monitor.is_activation_suppressed.__module__ == "wheel_publications_v2"
    assert runtime.monitor.telegram_api.__module__ == "personal_reminder_filter"
    assert runtime.monitor.fetch_all_sources.__module__ == "telegram_transport"
    assert runtime.monitor.fetch_public_channel.__module__ == "telegram_post_links_v2"
    assert runtime.monitor._bbvg_restart_duplicate_guard_installed is True
    assert runtime.monitor._bbvg_wheel_link_lifecycle_installed is True
    assert runtime.monitor._bbvg_wheel_lifecycle_v2_installed is True
    assert runtime.monitor.UNKNOWN_DEDUP_HOURS == 2
    assert notification_router._bbvg_notification_integrity_v2_installed is True

    primary = monitor_data.operational_sources(
        monitor.read_list(ROOT / "public_sources.txt"), "fast"
    )
    nightly = monitor_data.operational_sources(
        monitor.read_list(ROOT / "source_catalog.txt"), "nightly"
    )
    assert len({value.casefold() for value in primary + nightly}) >= 66
    assert not {value.casefold() for value in primary} & {
        value.casefold() for value in nightly
    }

    ledger = json.loads(
        (ROOT / "notification_delivery_state.json").read_text(encoding="utf-8")
    )
    assert ledger.get("format") == "bbvg-notification-delivery-v2"
    assert not {"chat_id", "user_id", "text", "url", "username"} & set(ledger)
    print("BB V.G. monitor v41 validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
