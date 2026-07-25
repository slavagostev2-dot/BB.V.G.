from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_production_entrypoint_cold_starts_and_reopens_storage(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "BOT_TOKEN": "cold-start-probe",
            "BOT_CHAT_ID": "0",
            "BBVG_COLD_START_PROBE": "true",
            "BBVG_RUNTIME_DIR": str(tmp_path),
        }
    )
    completed = subprocess.run(
        [sys.executable, "bbvg_monitor_main.py"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["production_entrypoint"] == "monitor.main"
    assert payload["source_count"] > 0
    assert payload["test_message_processed"] is True
    assert payload["heartbeat_written"] is True
    assert payload["graceful_restart"] is True
