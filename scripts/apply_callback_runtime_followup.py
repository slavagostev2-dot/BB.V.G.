from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(
            f"Expected exactly one follow-up target in {path}, found {text.count(old)}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "bbvg/bot/runtime.py",
    '''        if data.startswith(("bb:p:", "wheel:part:")):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            original_show_active = self.show_active
            self._edit_message_id = int(message.get("message_id") or 0) or None
            self.show_active = (  # type: ignore[method-assign]
                lambda page=0: self.show_menu(clear_stack=True)
            )
            try:
                super().handle_callback(query)
            finally:
                self.show_active = original_show_active  # type: ignore[method-assign]
                self._edit_message_id = previous_edit_message_id
            return
''',
    '''        if data.startswith("wheel:part:"):
            message = query.get("message") or {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            original_show_active = self.show_active
            self._edit_message_id = int(message.get("message_id") or 0) or None
            self.show_active = (  # type: ignore[method-assign]
                lambda page=0: self.show_menu(clear_stack=True)
            )
            try:
                super().handle_callback(query)
            finally:
                self.show_active = original_show_active  # type: ignore[method-assign]
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith("bb:p:"):
            super().handle_callback(query)
            return
''',
)

replace_once(
    "bbvg/bot/runtime.py",
    '''    panel.mark_personal_participation = lambda key: {"changed": True}  # type: ignore[method-assign]
    panel.answer = lambda query_id, text: callback_calls.append(("answer", text))  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: callback_calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel.handle_callback(
        {
            "id": "q",
            "data": "bb:p:token",
            "message": {"message_id": 77, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("menu", True) in callback_calls
    assert panel._edit_message_id is None
''',
    '''    panel.mark_personal_participation = lambda key: {"changed": True}  # type: ignore[method-assign]
    panel.answer = lambda query_id, text: callback_calls.append(("answer", text))  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: callback_calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel._delete_callback_message = lambda query: callback_calls.append(  # type: ignore[method-assign]
        ("delete", str(query.get("data") or ""))
    )
    panel.handle_callback(
        {
            "id": "q-notification",
            "data": "bb:p:token",
            "message": {"message_id": 77, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("delete", "bb:p:token") in callback_calls
    assert ("menu", True) not in callback_calls
    assert panel._edit_message_id is None

    callback_calls.clear()
    panel.handle_callback(
        {
            "id": "q-active",
            "data": "wheel:part:wheel-a",
            "message": {"message_id": 78, "chat": {"id": "1"}},
            "from": {"id": "1"},
        }
    )
    assert ("menu", True) in callback_calls
    assert panel._edit_message_id is None
''',
)

replace_once(
    "tests/test_production_stability_guardrails.py",
    '''    assert "def _notification_token" not in entrypoint
''',
    '''    assert "def _notification_token" not in entrypoint
    runtime = (ROOT / "bbvg/bot/runtime.py").read_text(encoding="utf-8")
    assert 'if data.startswith("wheel:part:")' in runtime
    assert 'if data.startswith("bb:p:")' in runtime
    assert 'if data.startswith(("bb:p:", "wheel:part:"))' not in runtime
''',
)

print("Callback navigation follow-up applied")
