from __future__ import annotations

import argparse
from typing import Any

import private_state
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v22 import TelegramPanelRuntimeV22
from admin_panel_v2 import default_access


class TelegramPanelRuntimeV23(TelegramPanelRuntimeV22):
    """Current v22 control center with private Worker + D1 state storage."""

    def load_access(self, force: bool = False) -> dict[str, Any]:
        with self.access_lock:
            if self.access_loaded and not force:
                return self.access
            value, _ = private_state.load_access(default_access())
            self.access = self.normalize_access(value)
            self.access_loaded = True
            return self.access

    def save_access(self, message: str = "Update Telegram panel access") -> None:
        del message
        with self.access_lock:
            normalized = self.normalize_access(self.access)
            private_state.save_access(normalized)
            self.access = normalized
            self.access_loaded = True

    def load_source_requests(self) -> dict[str, Any]:
        value = private_state.load_source_requests(default_source_requests())
        requests = value.get("requests") if isinstance(value, dict) else None
        return {
            "version": 1,
            "requests": requests if isinstance(requests, dict) else {},
        }

    def save_source_requests(self, value: dict[str, Any], message: str) -> None:
        del message
        private_state.save_source_requests(value)


def self_test() -> None:
    runtime = TelegramPanelRuntimeV23()
    access = default_access()
    access["owner_id"] = "1"
    access["users"] = {"1": {"id": "1", "chat_id": "1"}}
    writes: list[dict[str, Any]] = []
    original_load = private_state.load_access
    original_save = private_state.save_access
    original_load_requests = private_state.load_source_requests
    original_save_requests = private_state.save_source_requests
    try:
        private_state.load_access = lambda default=None: (access, True)  # type: ignore[assignment]
        private_state.save_access = lambda value: writes.append(value)  # type: ignore[assignment]
        private_state.load_source_requests = lambda default=None: {"version": 1, "requests": {}}  # type: ignore[assignment]
        private_state.save_source_requests = lambda value: writes.append(value)  # type: ignore[assignment]
        loaded = runtime.load_access(force=True)
        assert loaded["owner_id"] == "1"
        runtime.save_access()
        assert writes and writes[-1]["owner_id"] == "1"
        assert runtime.load_source_requests()["requests"] == {}
    finally:
        private_state.load_access = original_load  # type: ignore[assignment]
        private_state.save_access = original_save  # type: ignore[assignment]
        private_state.load_source_requests = original_load_requests  # type: ignore[assignment]
        private_state.save_source_requests = original_save_requests  # type: ignore[assignment]
    print("admin_panel_runtime_v23 private state self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not private_state.configured():
        raise SystemExit(
            "Private state API is not configured. Deploy state-api before starting the panel."
        )
    return TelegramPanelRuntimeV23().run()


if __name__ == "__main__":
    raise SystemExit(main())
