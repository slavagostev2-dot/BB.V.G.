from __future__ import annotations

import json
from pathlib import Path

from bbvg.ai_core import AIClient, AIConfig, FEATURE_NATURAL_LANGUAGE_ADMIN
from bbvg.bot import natural_language_admin as nla


def _client(tmp_path: Path, payload: dict) -> AIClient:
    config = AIConfig(
        enabled=True,
        enabled_features=frozenset({FEATURE_NATURAL_LANGUAGE_ADMIN}),
        provider="openai",
        model="test-model",
        timeout_seconds=5,
        max_calls_per_minute=10,
        cache_ttl_seconds=0,
        max_decisions=50,
        state_path=tmp_path / "ai.json",
        api_key="test-key",
    )
    return AIClient(
        config,
        transport=lambda *_: json.dumps(payload, ensure_ascii=False),
        clock=lambda: 1000.0,
    )


class Runtime:
    def __init__(self) -> None:
        self.pending_input = {}
        self.calls = []

    def set_context(self, chat_id, user_id):
        self.calls.append(("context", str(chat_id), str(user_id)))

    def role_for(self, user_id):
        return "owner"

    def show_active(self):
        self.calls.append(("active",))

    def show_status(self):
        self.calls.append(("status",))

    def show_ranking(self):
        self.calls.append(("ranking",))

    def show_inactive_report(self, page=0):
        self.calls.append(("inactive", page))

    def show_sources(self):
        self.calls.append(("sources",))

    def show_source_detail(self, source):
        self.calls.append(("source_detail", source))

    def show_profile(self):
        self.calls.append(("profile",))

    def with_nav(self, rows=None):
        return {"inline_keyboard": rows or []}

    def send(self, text, **kwargs):
        self.calls.append(("send", text, kwargs))
        return {}


def message(text: str) -> dict:
    return {
        "text": text,
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1},
    }


def test_read_only_intent_executes_immediately(tmp_path: Path) -> None:
    runtime = Runtime()
    handled = nla.handle_text(
        runtime,
        message("Покажи активные колёса"),
        client=_client(
            tmp_path,
            {
                "action": "show_active_wheels",
                "arguments": {},
                "confidence": 0.98,
                "reason": "read only",
            },
        ),
    )
    assert handled is True
    assert ("active",) in runtime.calls


def test_write_intent_only_creates_confirmation(tmp_path: Path) -> None:
    runtime = Runtime()
    handled = nla.handle_text(
        runtime,
        message("Поставь проверку раз в три минуты"),
        client=_client(
            tmp_path,
            {
                "action": "set_monitor_interval",
                "arguments": {"minutes": 3},
                "confidence": 0.96,
                "reason": "configuration change",
            },
        ),
    )
    assert handled is True
    sent = [call for call in runtime.calls if call[0] == "send"]
    assert len(sent) == 1
    markup = sent[0][2]["reply_markup"]
    callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    assert "nladmin:interval:3" in callbacks
    assert not any(call[0] == "set_interval" for call in runtime.calls)


def test_source_change_requires_confirmation_and_valid_username(tmp_path: Path) -> None:
    runtime = Runtime()
    handled = nla.handle_text(
        runtime,
        message("Добавь zont1x в основные источники"),
        client=_client(
            tmp_path,
            {
                "action": "set_source_mode",
                "arguments": {"source": "zont1x", "mode": "fast"},
                "confidence": 0.97,
                "reason": "source management",
            },
        ),
    )
    assert handled is True
    sent = [call for call in runtime.calls if call[0] == "send"]
    callback = sent[0][2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert callback == "nladmin:source:fast:zont1x"


def test_critical_intent_is_refused(tmp_path: Path) -> None:
    runtime = Runtime()
    handled = nla.handle_text(
        runtime,
        message("Перепиши историю git"),
        client=_client(
            tmp_path,
            {
                "action": "rewrite_git_history",
                "arguments": {},
                "confidence": 0.99,
                "reason": "critical",
            },
        ),
    )
    assert handled is True
    sent = [call for call in runtime.calls if call[0] == "send"]
    assert "не выполняется через AI-команды" in sent[0][1]


def test_unknown_or_low_confidence_falls_back_to_normal_bot(tmp_path: Path) -> None:
    runtime = Runtime()
    handled = nla.handle_text(
        runtime,
        message("Привет"),
        client=_client(
            tmp_path,
            {
                "action": "unknown",
                "arguments": {},
                "confidence": 0.4,
                "reason": "not a command",
            },
        ),
    )
    assert handled is False
