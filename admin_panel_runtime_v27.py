from __future__ import annotations

import argparse
from typing import Any

import bot_private_state
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v25 import TelegramPanelRuntimeV25
from admin_panel_runtime_v26 import TelegramPanelRuntimeV26


class TelegramPanelRuntimeV27(TelegramPanelRuntimeV26):
    """Production-safe v26 runtime with an isolated administrator callback test."""


def self_test() -> None:
    bot_private_state.self_test()

    callbacks = {
        button.get("callback_data")
        for row in TelegramPanelRuntimeV27.compact_menu_rows(True)
        for button in row
    }
    assert callbacks == {
        "page:stats:1",
        "page:active",
        "page:sources",
        "page:settings",
    }
    assert "page:discovery" not in callbacks
    assert "page:intelligence" not in callbacks

    source_callbacks = {
        "page:ranking",
        "page:intelligence",
        "page:discovery",
    }
    source_code = TelegramPanelRuntimeV25.show_sources.__code__
    constants = " ".join(str(value) for value in source_code.co_consts)
    assert all(value in constants for value in source_callbacks)

    panel = TelegramPanelRuntimeV27()
    access = panel._bootstrap_access(
        {
            "owner_id": "1",
            "users": {
                "1": {
                    "id": "1",
                    "chat_id": "1",
                    "first_name": "Owner",
                    "notifications_enabled": True,
                }
            },
        }
    )
    panel._bot_bundle = bot_private_state.default_bundle(
        access,
        default_source_requests(),
    )
    # Do not force a disk reload here. Production contains the real encrypted
    # user bundle, while this test must remain completely isolated from it.
    panel.load_access()

    actions: list[tuple[str, str]] = []
    saves: list[str] = []
    panel._save_bot_bundle = lambda message: saves.append(message) or True  # type: ignore[method-assign]
    panel.dispatch_admin_action = (  # type: ignore[method-assign]
        lambda action, value: actions.append((action, value)) or {"detail": "ok"}
    )
    panel.answer = lambda *args, **kwargs: None  # type: ignore[method-assign]
    panel.send = lambda *args, **kwargs: {"ok": True}  # type: ignore[method-assign]
    panel.refresh_snapshot = lambda: None  # type: ignore[method-assign]
    panel.show_active = lambda: None  # type: ignore[method-assign]

    query: dict[str, Any] = {
        "id": "callback-test",
        "from": {"id": 1, "username": "owner", "first_name": "Owner"},
        "message": {
            "message_id": 1,
            "chat": {"id": 1, "type": "private"},
        },
    }
    panel.handle_callback({**query, "data": "wheel:part:test-wheel"})
    panel.handle_callback({**query, "data": "wheel:inactive:test-wheel"})

    assert actions == [
        ("participate_wheel", "test-wheel"),
        ("mark_inactive_global", "test-wheel|1"),
    ]
    assert saves == []
    print("admin_panel_runtime_v27 production administrator button self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV27().run()


if __name__ == "__main__":
    raise SystemExit(main())
