from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import admin_bot as legacy
import private_state
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v22 import TelegramPanelRuntimeV22
from admin_panel_v2 import default_access

UTC = timezone.utc


class TelegramPanelRuntimeV23(TelegramPanelRuntimeV22):
    """Current control center with D1 storage and a private in-memory fallback."""

    def __init__(self) -> None:
        super().__init__()
        self._temporary_source_requests = default_source_requests()

    @staticmethod
    def temporary_access() -> dict[str, Any]:
        value = default_access()
        owner_id = str(legacy.ADMIN_USER_ID or legacy.BOT_CHAT_ID or "").strip()
        chat_id = str(legacy.BOT_CHAT_ID or owner_id).strip()
        if owner_id:
            now = datetime.now(UTC).isoformat()
            value["owner_id"] = owner_id
            value["notification_recipients"] = [chat_id or owner_id]
            value["users"] = {
                owner_id: {
                    "id": owner_id,
                    "chat_id": chat_id or owner_id,
                    "username": "",
                    "first_name": "Администратор",
                    "last_name": "",
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "notifications_enabled": True,
                }
            }
        return value

    def load_access(self, force: bool = False) -> dict[str, Any]:
        with self.access_lock:
            if self.access_loaded and (not force or not private_state.configured()):
                return self.access
            if private_state.configured():
                value, _ = private_state.load_access(default_access())
            else:
                value = self.access if self.access_loaded else self.temporary_access()
            self.access = self.normalize_access(value)
            self.access_loaded = True
            return self.access

    def save_access(self, message: str = "Update Telegram panel access") -> None:
        del message
        with self.access_lock:
            normalized = self.normalize_access(self.access)
            if private_state.configured():
                private_state.save_access(normalized)
            self.access = normalized
            self.access_loaded = True

    def load_source_requests(self) -> dict[str, Any]:
        if private_state.configured():
            value = private_state.load_source_requests(default_source_requests())
        else:
            value = self._temporary_source_requests
        requests = value.get("requests") if isinstance(value, dict) else None
        return {
            "version": 1,
            "requests": requests if isinstance(requests, dict) else {},
        }

    def save_source_requests(self, value: dict[str, Any], message: str) -> None:
        del message
        if private_state.configured():
            private_state.save_source_requests(value)
        else:
            self._temporary_source_requests = value


def self_test() -> None:
    runtime = TelegramPanelRuntimeV23()
    access = default_access()
    access["owner_id"] = "1"
    access["users"] = {"1": {"id": "1", "chat_id": "1"}}
    writes: list[dict[str, Any]] = []
    original_configured = private_state.configured
    original_load = private_state.load_access
    original_save = private_state.save_access
    original_load_requests = private_state.load_source_requests
    original_save_requests = private_state.save_source_requests
    try:
        private_state.configured = lambda: True  # type: ignore[assignment]
        private_state.load_access = lambda default=None: (access, True)  # type: ignore[assignment]
        private_state.save_access = lambda value: writes.append(value)  # type: ignore[assignment]
        private_state.load_source_requests = lambda default=None: {"version": 1, "requests": {}}  # type: ignore[assignment]
        private_state.save_source_requests = lambda value: writes.append(value)  # type: ignore[assignment]
        loaded = runtime.load_access(force=True)
        assert loaded["owner_id"] == "1"
        runtime.save_access()
        assert writes and writes[-1]["owner_id"] == "1"
        assert runtime.load_source_requests()["requests"] == {}

        private_state.configured = lambda: False  # type: ignore[assignment]
        fallback = TelegramPanelRuntimeV23()
        fallback.access = fallback.normalize_access(access)
        fallback.access_loaded = True
        fallback.save_access()
        fallback.save_source_requests({"version": 1, "requests": {"a": {"id": "a"}}}, "test")
        assert "a" in fallback.load_source_requests()["requests"]
    finally:
        private_state.configured = original_configured  # type: ignore[assignment]
        private_state.load_access = original_load  # type: ignore[assignment]
        private_state.save_access = original_save  # type: ignore[assignment]
        private_state.load_source_requests = original_load_requests  # type: ignore[assignment]
        private_state.save_source_requests = original_save_requests  # type: ignore[assignment]
    print("admin_panel_runtime_v23 private state and fallback self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not private_state.configured():
        print("WARNING private state is unavailable; using non-persistent in-memory access")
    return TelegramPanelRuntimeV23().run()


if __name__ == "__main__":
    raise SystemExit(main())
