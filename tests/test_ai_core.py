from __future__ import annotations

import json
from pathlib import Path

from bbvg.ai_core import (
    AIClient,
    AIConfig,
    FEATURE_HEALTH_INSPECTOR,
    FEATURE_SUSPICIOUS_POST_ANALYSIS,
)


def make_config(path: Path, *, enabled: bool = True, limit: int = 10) -> AIConfig:
    return AIConfig(
        enabled=enabled,
        enabled_features=frozenset(
            {FEATURE_HEALTH_INSPECTOR, FEATURE_SUSPICIOUS_POST_ANALYSIS}
        ),
        provider="openai",
        model="test-model",
        timeout_seconds=5,
        max_calls_per_minute=limit,
        cache_ttl_seconds=300,
        max_decisions=50,
        state_path=path,
        api_key="test-key",
    )


def test_disabled_feature_uses_fallback_without_state_write(tmp_path: Path) -> None:
    state_path = tmp_path / "ai.json"
    config = make_config(state_path, enabled=False)
    called = []
    client = AIClient(config, transport=lambda *_: called.append(True) or "unexpected")

    result = client.ask_text(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt="Explain health.",
        user_input="ok",
        fallback_text="rules-only",
    )

    assert result.ok is False
    assert result.status == "disabled"
    assert result.text == "rules-only"
    assert result.used_fallback is True
    assert called == []
    assert not state_path.exists()


def test_cache_avoids_second_provider_call_and_logs_decisions(tmp_path: Path) -> None:
    state_path = tmp_path / "ai.json"
    calls = []

    def transport(_config: AIConfig, prompt: str) -> str:
        calls.append(prompt)
        return "healthy"

    client = AIClient(make_config(state_path), transport=transport, clock=lambda: 1000.0)
    first = client.ask_text(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt="Explain health.",
        user_input="snapshot",
    )
    second = client.ask_text(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt="Explain health.",
        user_input="snapshot",
    )

    assert first.ok is True and first.cached is False
    assert second.ok is True and second.cached is True
    assert calls == ["SYSTEM INSTRUCTIONS:\nExplain health.\n\nINPUT:\nsnapshot"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [row["status"] for row in state["decisions"]] == ["ok", "cache_hit"]
    assert all("prompt" not in row for row in state["decisions"])


def test_rate_limit_falls_back_without_second_provider_call(tmp_path: Path) -> None:
    state_path = tmp_path / "ai.json"
    calls = []
    client = AIClient(
        make_config(state_path, limit=1),
        transport=lambda _config, prompt: calls.append(prompt) or "first",
        clock=lambda: 2000.0,
    )

    first = client.ask_text(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt="A",
        user_input="one",
    )
    second = client.ask_text(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt="A",
        user_input="two",
        fallback_text="fallback",
    )

    assert first.ok is True
    assert second.ok is False
    assert second.status == "rate_limited"
    assert second.text == "fallback"
    assert second.used_fallback is True
    assert len(calls) == 1


def test_structured_response_is_parsed_and_decision_is_annotated(tmp_path: Path) -> None:
    state_path = tmp_path / "ai.json"
    client = AIClient(
        make_config(state_path),
        transport=lambda *_: json.dumps(
            {
                "classification": "possible_wheel_announcement",
                "confidence": 0.84,
                "reason": "wheel wording",
            }
        ),
        clock=lambda: 3000.0,
    )

    result = client.ask_json(
        FEATURE_SUSPICIOUS_POST_ANALYSIS,
        system_prompt="Classify.",
        user_input="Soon we spin.",
        fallback_data={"classification": "uncertain", "confidence": 0.0},
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data["classification"] == "possible_wheel_announcement"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    decision = state["decisions"][-1]
    assert decision["classification"] == "possible_wheel_announcement"
    assert decision["confidence"] == 0.84
    assert decision["reason"] == "wheel wording"


def test_provider_error_returns_deterministic_fallback(tmp_path: Path) -> None:
    state_path = tmp_path / "ai.json"

    def broken(_config: AIConfig, _prompt: str) -> str:
        raise RuntimeError("boom")

    client = AIClient(make_config(state_path), transport=broken, clock=lambda: 4000.0)
    result = client.ask_text(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt="Explain.",
        user_input="snapshot",
        fallback_text="rules-only",
    )

    assert result.ok is False
    assert result.status == "provider_error"
    assert result.text == "rules-only"
    assert result.used_fallback is True
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["decisions"][-1]["status"] == "provider_error"
