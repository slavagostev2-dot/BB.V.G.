from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def patch_monitor_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "monitor.yml"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '          REQUEST_TIMEOUT_SECONDS: "10"\n',
        '          REQUEST_TIMEOUT_SECONDS: "10"\n'
        '          MONITOR_INTERVAL_MINUTES: "1"\n',
        label="monitor interval environment",
    )

    text = replace_once(
        text,
        """          runtime_files=(
            state.json source_health.json source_stats.json
            unknown_timer_samples.json monitor_status.json
            notification_delivery_state.json ai_runtime_state.json
          )
""",
        """          runtime_files=(
            state.json source_health.json source_stats.json
            unknown_timer_samples.json ai_runtime_state.json
          )
""",
        label="runtime file ownership",
    )

    text = replace_once(
        text,
        """            git add "${runtime_files[@]}"
            git commit -m "Update BB V.G. runtime data [skip ci]" || true

            for attempt in 1 2 3; do
""",
        """            git add "${runtime_files[@]}"
            git commit -m "Update BB V.G. runtime data [skip ci]" || true

            # These files have their own Contents API checkpoints. Leaving their
            # local copies dirty blocks `git pull --rebase` and can prevent
            # state.json from reaching Control Center after a wheel is found.
            git restore --source=HEAD -- \
              monitor_status.json notification_delivery_state.json || true

            for attempt in 1 2 3; do
""",
        label="independent checkpoint cleanup",
    )

    text = replace_once(
        text,
        '                echo "WARNING monitor_status.json could not be published independently; bulk runtime sync will retry it."\n',
        '                echo "WARNING monitor_status.json could not be published independently; the next heartbeat will retry it."\n',
        label="heartbeat warning",
    )

    text = replace_once(
        text,
        """            interval_seconds=$(python - <<'PY'
          import bot_notification_state
          try:
              data, _ = bot_notification_state.load_config()
              value = int(data.get("settings", {}).get("monitor_interval_minutes", 5))
          except Exception:
              value = 5
          value = value if value in {1, 3, 5, 10, 15, 30} else 5
          print(value * 60)
          PY
            )
""",
        """            interval_seconds=$(python - <<'PY'
          import os
          import bot_notification_state

          allowed = {1, 3, 5, 10, 15, 30}
          try:
              fallback = int(os.getenv("MONITOR_INTERVAL_MINUTES", "1"))
          except (TypeError, ValueError):
              fallback = 1
          fallback = fallback if fallback in allowed else 1

          try:
              data, _ = bot_notification_state.load_config()
              value = int(
                  data.get("settings", {}).get(
                      "monitor_interval_minutes", fallback
                  )
              )
          except Exception as exc:
              print(
                  f"WARNING monitor interval config unavailable: "
                  f"{type(exc).__name__}; using {fallback} minute(s)",
                  file=sys.stderr,
              )
              value = fallback
          value = value if value in allowed else fallback
          print(value * 60)
          PY
            )
""".replace("          import os\n", "          import os\n          import sys\n", 1),
        label="one minute fallback",
    )

    path.write_text(text, encoding="utf-8")


def patch_admin_diagnostic() -> None:
    path = ROOT / "admin_panel_v2.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'MONITOR_INTERVAL_MINUTES = max(1, int(os.getenv("MONITOR_INTERVAL_MINUTES", "5")))',
        'MONITOR_INTERVAL_MINUTES = max(1, int(os.getenv("MONITOR_INTERVAL_MINUTES", "1")))',
        label="admin interval default",
    )

    text = replace_once(
        text,
        """DEFAULT_SETTINGS = {
    "public_panel": True,
    "notifications": True,
}
""",
        """DEFAULT_SETTINGS = {
    "public_panel": True,
    "notifications": True,
    "monitor_interval_minutes": MONITOR_INTERVAL_MINUTES,
}
""",
        label="admin default settings",
    )

    text = replace_once(
        text,
        """            settings["notifications"] = bool(
                raw_settings.get(
                    "notifications",
                    raw_settings.get("wheel_notifications", DEFAULT_SETTINGS["notifications"]),
                )
            )
        result["settings"] = settings
""",
        """            settings["notifications"] = bool(
                raw_settings.get(
                    "notifications",
                    raw_settings.get("wheel_notifications", DEFAULT_SETTINGS["notifications"]),
                )
            )
            try:
                interval = int(
                    raw_settings.get(
                        "monitor_interval_minutes",
                        DEFAULT_SETTINGS["monitor_interval_minutes"],
                    )
                )
            except (TypeError, ValueError):
                interval = DEFAULT_SETTINGS["monitor_interval_minutes"]
            settings["monitor_interval_minutes"] = (
                interval if interval in {1, 3, 5, 10, 15, 30}
                else DEFAULT_SETTINGS["monitor_interval_minutes"]
            )
        result["settings"] = settings
""",
        label="admin interval normalization",
    )

    text = replace_once(
        text,
        """        primary = {x.casefold() for x in snap.fast}
        reserve = {x.casefold() for x in snap.nightly}
        mode = "основная проверка каждые 5 минут" if source.casefold() in primary else (
            "резервная проверка" if source.casefold() in reserve else "не добавлен в мониторинг"
        )
""",
        """        primary = {x.casefold() for x in snap.fast}
        reserve = {x.casefold() for x in snap.nightly}
        settings = self.load_access().get("settings", {})
        try:
            interval = int(
                settings.get(
                    "monitor_interval_minutes",
                    MONITOR_INTERVAL_MINUTES,
                )
            )
        except (TypeError, ValueError):
            interval = MONITOR_INTERVAL_MINUTES
        interval = interval if interval in {1, 3, 5, 10, 15, 30} else MONITOR_INTERVAL_MINUTES
        interval_label = "минуту" if interval == 1 else f"{interval} минут"
        mode = f"основная проверка каждую {interval_label}" if source.casefold() in primary else (
            "резервная проверка" if source.casefold() in reserve else "не добавлен в мониторинг"
        )
""",
        label="diagnostic interval",
    )

    path.write_text(text, encoding="utf-8")


def add_regression_test() -> None:
    path = ROOT / "tests" / "test_monitor_interval_runtime_sync.py"
    path.write_text(
        '''from pathlib import Path\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_monitor_uses_one_minute_fallback_and_separate_checkpoints() -> None:\n    workflow = (ROOT / ".github/workflows/monitor.yml").read_text(encoding="utf-8")\n    assert 'MONITOR_INTERVAL_MINUTES: "1"' in workflow\n    assert 'os.getenv("MONITOR_INTERVAL_MINUTES", "1")' in workflow\n    assert 'using {fallback} minute(s)' in workflow\n\n    runtime_block = workflow.split("runtime_files=(", 1)[1].split(")", 1)[0]\n    assert "state.json" in runtime_block\n    assert "source_health.json" in runtime_block\n    assert "monitor_status.json" not in runtime_block\n    assert "notification_delivery_state.json" not in runtime_block\n    assert "git restore --source=HEAD" in workflow\n    assert "monitor_status.json notification_delivery_state.json" in workflow\n\n\ndef test_diagnostic_reports_configured_interval() -> None:\n    source = (ROOT / "admin_panel_v2.py").read_text(encoding="utf-8")\n    assert 'MONITOR_INTERVAL_MINUTES", "1"' in source\n    assert '"monitor_interval_minutes": MONITOR_INTERVAL_MINUTES' in source\n    assert 'settings.get(' in source\n    assert '"monitor_interval_minutes",' in source\n    assert 'interval_label = "минуту" if interval == 1' in source\n    assert "основная проверка каждые 5 минут" not in source\n''',
        encoding="utf-8",
    )


def main() -> None:
    patch_monitor_workflow()
    patch_admin_diagnostic()
    add_regression_test()


if __name__ == "__main__":
    main()
