from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ActiveButtonRetryTests(unittest.TestCase):
    def test_retry_policy_uses_existing_dispatcher_without_leaking_runtime_patches(self) -> None:
        script = r'''
import json
from datetime import datetime, timedelta, timezone
import bbvg_monitor_main as runtime

UTC = timezone.utc
current = datetime(2026, 7, 24, 14, 5, tzinfo=UTC)
runtime.monitor.now_utc = lambda: current
entry = {
    "deadline": (current + timedelta(minutes=12)).isoformat(),
    "verification_status": runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
    "url": "https://betboom.ru/freestream/deko2",
}
record = {
    "status": "button_not_found",
    "attempt_version": 2,
    "attempted_at": (current - timedelta(minutes=3)).isoformat(),
}
results = [runtime.recoverable_active_button_not_found(record, entry)]

current = current + timedelta(minutes=3)
runtime.monitor.now_utc = lambda: current
record["attempted_at"] = (current - timedelta(minutes=3)).isoformat()
results.append(runtime.recoverable_active_button_not_found(record, entry))

current = current + timedelta(minutes=3)
runtime.monitor.now_utc = lambda: current
record["attempted_at"] = (current - timedelta(minutes=3)).isoformat()
results.append(runtime.recoverable_active_button_not_found(record, entry))

near_deadline = {
    "deadline": (current + timedelta(minutes=1)).isoformat(),
    "verification_status": runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
    "url": "https://betboom.ru/freestream/deko2",
}
near_record = dict(record)
near_record["attempted_at"] = (current - timedelta(minutes=3)).isoformat()
near_result = runtime.recoverable_active_button_not_found(near_record, near_deadline)

recent = {
    "deadline": (current + timedelta(minutes=8)).isoformat(),
    "verification_status": runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
    "url": "https://betboom.ru/freestream/deko2",
}
recent_record = dict(record)
recent_record["attempted_at"] = (current - timedelta(minutes=1)).isoformat()
recent_result = runtime.recoverable_active_button_not_found(recent_record, recent)

legacy = dict(record)
legacy["attempt_version"] = 1
legacy_result = runtime.recoverable_active_button_not_found(legacy, {
    "deadline": (current + timedelta(minutes=8)).isoformat(),
    "verification_status": runtime.monitor.WHEEL_VERIFICATION_CONFIRMED,
    "url": "https://betboom.ru/freestream/deko2",
})

print(json.dumps({
    "results": results,
    "retry_count": entry.get("auto_participation_button_retry_count"),
    "near_deadline": near_result,
    "recent": recent_result,
    "legacy": legacy_result,
}))
'''
        env = os.environ.copy()
        env.update(
            {
                "BOT_TOKEN": "test-bot-token",
                "BOT_STATE_KEY": "test-state-key",
                "BOT_CHAT_ID": "1",
                "ADMIN_USER_ID": "1",
                "BBVG_TEST_MODE": "1",
                "TELEGRAM_WEB_DOMAIN": "telegram.me",
            }
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(
            payload["results"],
            [
                "active_button_not_found_retry",
                "active_button_not_found_retry",
                "",
            ],
        )
        self.assertEqual(payload["retry_count"], 2)
        self.assertEqual(payload["near_deadline"], "")
        self.assertEqual(payload["recent"], "")
        self.assertEqual(payload["legacy"], "browser_attempt_upgrade")


if __name__ == "__main__":
    unittest.main()
