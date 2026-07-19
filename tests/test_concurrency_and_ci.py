from __future__ import annotations

import base64
import json
import multiprocessing
import os
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from tests._bootstrap import install_optional_dependency_stubs

install_optional_dependency_stubs()

import admin_action_queue
import bot_private_state
import monitor_data
import notification_integrity_v2
from bbvg.bot.storage import PrivateStateRuntime, _merge_value
from ci_verify_current_commit import verify_current_commit


ROOT = Path(__file__).resolve().parents[1]


def _claim_in_process(
    path: str,
    digest: str,
    start: Any,
    results: Any,
) -> None:
    start.wait(5)
    results.put(notification_integrity_v2.claim_delivery(digest, Path(path)))


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

    def test_source_request_deletion_and_remote_addition_both_survive(self) -> None:
        base = {"version": 1, "requests": {"old": {"source": "old"}}}
        local = {"version": 1, "requests": {}}
        remote = {
            "version": 1,
            "requests": {
                "old": {"source": "old"},
                "new": {"source": "new"},
            },
        }
        merged = _merge_value(base, local, remote)
        self.assertEqual(merged["requests"], {"new": {"source": "new"}})

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
            return Response({1: 409, 2: 422}.get(len(puts), 200))

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

        self.assertEqual(len(puts), 3)
        queued_ids = []
        for request in puts:
            decoded = json.loads(base64.b64decode(request["content"]))
            queued_ids.extend(decoded["commands"])
        self.assertEqual(set(queued_ids), {command_id})

    def test_queue_cleanup_is_bounded_and_keeps_latest_commands(self) -> None:
        original = admin_action_queue.MAX_COMMANDS
        admin_action_queue.MAX_COMMANDS = 3
        try:
            queue = admin_action_queue.default_queue()
            for index in range(5):
                queue, _ = admin_action_queue.append_command(
                    queue,
                    "recheck_wheel",
                    f"wheel-{index}",
                    command_id=f"command-{index}",
                )
        finally:
            admin_action_queue.MAX_COMMANDS = original
        self.assertEqual(list(queue["commands"]), ["command-2", "command-3", "command-4"])
        self.assertEqual(queue["sequence"], 5)

    def test_atomic_json_failure_preserves_last_valid_revision(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            monitor_data.atomic_write_json(path, {"version": 1, "value": "stable"})
            with patch("monitor_data.os.replace", side_effect=OSError("crash before replace")):
                with self.assertRaises(OSError):
                    monitor_data.atomic_write_json(path, {"version": 1, "value": "new"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "stable")
            self.assertEqual(list(path.parent.glob(".state.json.*.tmp")), [])

    def test_encrypted_state_failure_and_wrong_key_preserve_valid_bundle(self) -> None:
        original_path = bot_private_state.STATE_PATH
        try:
            with TemporaryDirectory() as temporary:
                bot_private_state.STATE_PATH = Path(temporary) / "state.enc.json"
                stable = {
                    "version": 2,
                    "access": {"owner_id": "1", "users": {"1": {"chat_id": "10"}}},
                    "source_requests": {"version": 1, "requests": {}},
                }
                bot_private_state.save_file(stable, secret="correct-key")
                with patch(
                    "bot_private_state.os.replace",
                    side_effect=OSError("crash before replace"),
                ):
                    with self.assertRaises(OSError):
                        bot_private_state.save_file(
                            {**stable, "access": {"owner_id": "2", "users": {}}},
                            secret="correct-key",
                        )
                restored = bot_private_state.load_file(secret="correct-key")
                self.assertEqual(restored["access"]["owner_id"], "1")
                with self.assertRaises(bot_private_state.BotStateIntegrityError):
                    bot_private_state.load_file(secret="wrong-key")
        finally:
            bot_private_state.STATE_PATH = original_path

    def test_interprocess_delivery_claim_has_one_winner(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "notification_delivery_state.json"
            digest = "a" * 64
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=_claim_in_process,
                    args=(str(path), digest, start, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            values = sorted(results.get(timeout=10) for _ in processes)
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(values, [False, True])
            state = notification_integrity_v2.load_state(path)
            self.assertIn(digest, state["claims"])
            notification_integrity_v2.release_delivery(digest, path)
            self.assertTrue(notification_integrity_v2.claim_delivery(digest, path))
            notification_integrity_v2.release_delivery(digest, path)

    def test_all_tracked_json_has_an_owner_and_compatible_schema(self) -> None:
        self.assertEqual(len(monitor_data.JSON_STATE_CONTRACTS), 28)
        self.assertEqual(monitor_data.validate_json_state_contracts(ROOT), [])

    def test_only_discovery_is_an_automatic_source_catalog_writer(self) -> None:
        discovery = (ROOT / ".github/workflows/nightly-discovery.yml").read_text(
            encoding="utf-8"
        )
        tier = (ROOT / ".github/workflows/source-tier-maintenance.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("group: bb-vg-source-catalog-writer", discovery)
        self.assertIn("public_sources.txt source_catalog.txt", discovery)
        self.assertIn("group: bb-vg-source-tier-audit", tier)
        self.assertIn("files=(source_tier_state.json)", tier)
        self.assertNotIn("files=(public_sources.txt source_catalog.txt", tier)
        panel = (ROOT / ".github/workflows/admin-bot.yml").read_text(encoding="utf-8")
        self.assertIn("files=(bot_private_state.enc.json)", panel)
        self.assertNotIn("notification_integrity_v2.py --prune", panel)
        self.assertNotIn(
            "files=(bot_private_state.enc.json notification_delivery_state.json)",
            panel,
        )
        rotation = (ROOT / ".github/workflows/rotate-bot-state-key.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("group: bb-vg-telegram-admin-panel", panel)
        self.assertIn("group: bb-vg-telegram-admin-panel", rotation)


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
