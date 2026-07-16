from __future__ import annotations

import base64
import json
import os
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_queue
from bbvg.bot.storage import PrivateStateRuntime, _merge_value
from ci_verify_current_commit import verify_current_commit


ROOT = Path(__file__).resolve().parents[1]


class ConcurrentStateTests(unittest.TestCase):
    def test_registration_and_personal_action_merge_without_data_loss(self) -> None:
        panel = PrivateStateRuntime()
        base = panel.normalize_access(
            {
                "owner_id": "1",
                "admins": [],
                "blocked_users": [],
                "notification_recipients": ["101"],
                "settings": {"notifications": True},
                "users": {
                    "1": {
                        "id": "1",
                        "chat_id": "101",
                        "participating_wheels": {},
                    }
                },
            }
        )
        local = deepcopy(base)
        local["users"]["1"]["participating_wheels"] = {
            "wheel-a": {"joined_at": "2026-07-15T10:00:00+00:00"}
        }
        remote = deepcopy(base)
        remote["users"]["2"] = {
            "id": "2",
            "chat_id": "202",
            "username": "new_user",
        }
        remote["users"]["1"]["last_seen_at"] = "2026-07-15T10:01:00+00:00"
        merged = panel._merge_access(base, local, remote)
        self.assertIn("2", merged["users"], "concurrent registration was erased")
        self.assertIn(
            "wheel-a",
            merged["users"]["1"]["participating_wheels"],
            "personal participation was erased",
        )
        self.assertEqual(
            merged["users"]["1"]["last_seen_at"],
            "2026-07-15T10:01:00+00:00",
        )

    def test_two_source_requests_merge_instead_of_overwriting(self) -> None:
        base = {"version": 1, "requests": {}}
        local = {"version": 1, "requests": {"local": {"source": "first"}}}
        remote = {"version": 1, "requests": {"remote": {"source": "second"}}}
        merged = _merge_value(base, local, remote)
        self.assertEqual(set(merged["requests"]), {"local", "remote"})

    def test_remote_queue_retries_conflict_with_same_command(self) -> None:
        original_get = admin_action_queue.requests.get
        original_put = admin_action_queue.requests.put
        original_env = {
            name: os.environ.get(name)
            for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_BRANCH")
        }
        puts: list[dict[str, Any]] = []

        class Response:
            def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
                self.status_code = status
                self._payload = payload or {}

            def json(self) -> dict[str, Any]:
                return self._payload

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

        empty = json.dumps(admin_action_queue.default_queue()).encode("utf-8")
        payload = {"sha": "base-sha", "content": base64.b64encode(empty).decode("ascii")}
        admin_action_queue.requests.get = lambda *args, **kwargs: Response(200, payload)

        def put(*args: Any, **kwargs: Any) -> Response:
            puts.append(deepcopy(kwargs["json"]))
            return Response(409 if len(puts) == 1 else 200)

        admin_action_queue.requests.put = put
        os.environ.update(
            {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_BRANCH": "main",
            }
        )
        try:
            command_id = admin_action_queue.enqueue_remote("recheck_wheel", "wheel-a")
        finally:
            admin_action_queue.requests.get = original_get
            admin_action_queue.requests.put = original_put
            for name, value in original_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(len(puts), 2)
        queued_ids = []
        for request in puts:
            decoded = json.loads(base64.b64decode(request["content"]))
            queued_ids.extend(decoded["commands"])
        self.assertEqual(set(queued_ids), {command_id})


class CurrentCommitTests(unittest.TestCase):
    def test_current_checkout_is_verified(self) -> None:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        self.assertEqual(verify_current_commit(head, ROOT), head)

    def test_wrong_checkout_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            verify_current_commit("0" * 40, ROOT)


if __name__ == "__main__":
    unittest.main()
