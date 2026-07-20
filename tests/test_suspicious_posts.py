from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bbvg.ai_core import AIClient, AIConfig, FEATURE_SUSPICIOUS_POST_ANALYSIS
from bbvg.monitor import suspicious_posts

UTC = timezone.utc


def _client(tmp_path: Path, payload: dict, calls: list[str]) -> AIClient:
    config = AIConfig(
        enabled=True,
        enabled_features=frozenset({FEATURE_SUSPICIOUS_POST_ANALYSIS}),
        provider="openai",
        model="test-model",
        timeout_seconds=5,
        max_calls_per_minute=10,
        cache_ttl_seconds=300,
        max_decisions=50,
        state_path=tmp_path / "ai-core.json",
        api_key="test-key",
    )

    def transport(_config: AIConfig, prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(payload, ensure_ascii=False)

    return AIClient(config, transport=transport, clock=lambda: 1000.0)


def _post(text: str) -> suspicious_posts.SuspiciousPost:
    return suspicious_posts.SuspiciousPost(
        source="example",
        message_id=123,
        date=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
        text=text,
        message_url="https://telegram.me/example/123",
    )


def test_candidate_gate_ignores_direct_links_and_plain_posts() -> None:
    assert suspicious_posts.is_candidate("Сегодня крутим колесо, ссылка будет позже")
    assert suspicious_posts.is_candidate("BetBoom сегодня: розыгрыш и колесо")
    assert not suspicious_posts.is_candidate("Обычный пост про расписание стрима")
    assert not suspicious_posts.is_candidate(
        "Колесо https://betboom.ru/freestream/example"
    )


def test_high_confidence_announcement_creates_admin_review_only(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(
        tmp_path,
        {
            "classification": "possible_wheel_announcement",
            "confidence": 0.93,
            "reason": "Указано будущее колесо и обещана ссылка позже",
        },
        calls,
    )
    state: dict = {"sentinel": "unchanged"}
    summary = suspicious_posts.analyze_posts(
        [_post("Сегодня крутим колесо, ссылка будет позже")],
        state,
        client=client,
        current=datetime(2026, 7, 20, 10, 5, tzinfo=UTC),
    )

    assert summary["analyzed"] == 1
    assert len(summary["alerts"]) == 1
    assert state["sentinel"] == "unchanged"
    assert "active_wheels" not in state
    assert "pending_posts" not in state
    assert len(calls) == 1


def test_same_post_is_not_reclassified_and_unsent_alert_can_retry(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(
        tmp_path,
        {
            "classification": "possible_wheel_announcement",
            "confidence": 0.91,
            "reason": "Анонс",
        },
        calls,
    )
    state: dict = {}
    post = _post("Сегодня колесо, ссылку дадим позже")
    current = datetime(2026, 7, 20, 10, 5, tzinfo=UTC)

    first = suspicious_posts.analyze_posts([post], state, client=client, current=current)
    second = suspicious_posts.analyze_posts([post], state, client=client, current=current)

    assert len(calls) == 1
    assert len(first["alerts"]) == 1
    assert len(second["alerts"]) == 1
    key = second["alerts"][0]["record_key"]
    assert suspicious_posts.mark_alert_notified(state, key)
    third = suspicious_posts.analyze_posts([post], state, client=client, current=current)
    assert third["alerts"] == []


def test_low_confidence_and_invalid_category_do_not_alert(tmp_path: Path) -> None:
    for payload in (
        {"classification": "possible_wheel_announcement", "confidence": 0.55, "reason": "Слабо"},
        {"classification": "unsupported", "confidence": 0.99, "reason": "Неверная категория"},
    ):
        calls: list[str] = []
        state: dict = {}
        summary = suspicious_posts.analyze_posts(
            [_post("Сегодня крутим колесо, ссылка позже")],
            state,
            client=_client(tmp_path, payload, calls),
            current=datetime(2026, 7, 20, 10, 5, tzinfo=UTC),
        )
        assert summary["alerts"] == []


def test_disabled_feature_does_not_call_provider(tmp_path: Path) -> None:
    calls: list[str] = []
    config = AIConfig(
        enabled=False,
        enabled_features=frozenset(),
        provider="openai",
        model="test-model",
        state_path=tmp_path / "ai-core.json",
        api_key="test-key",
    )
    client = AIClient(config, transport=lambda *_: calls.append("called") or "{}")
    state: dict = {}
    summary = suspicious_posts.analyze_posts(
        [_post("Сегодня крутим колесо, ссылка позже")],
        state,
        client=client,
        current=datetime(2026, 7, 20, 10, 5, tzinfo=UTC),
    )
    assert summary["status"] == "disabled"
    assert calls == []
    assert state == {}
