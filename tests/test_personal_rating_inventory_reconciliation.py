from __future__ import annotations

from datetime import datetime, timezone

import monitor_data
import personal_wheel_voting
from bbvg import reconciliation


UTC = timezone.utc


def _vote(user_id: str, *, role: str, weight: int, sources: list[str]) -> dict[str, object]:
    return {
        "wheel_key": "wheel-a",
        "event_key": "wheel-a#action:10",
        "actor": personal_wheel_voting.actor_vote_token(user_id, secret="test-secret"),
        "role": role,
        "weight": weight,
        "sources": sources,
        "voted_at": datetime(2026, 9, 5, 6, 0, tzinfo=UTC).isoformat(),
    }


def test_repair_removes_synthetic_vote_source_and_preserves_operations(
    tmp_path, monkeypatch
) -> None:
    inventory = tmp_path / "public_sources.txt"
    inventory.write_text("RealSource\n", encoding="utf-8")
    monkeypatch.setattr(monitor_data, "PUBLIC_SOURCES_PATH", inventory)

    stats = {
        "version": 1,
        "source_rating_policy": personal_wheel_voting.PERSONAL_RATING_POLICY,
        "personal_wheel_votes": {
            "user-vote": _vote(
                "100", role="user", weight=1, sources=["bbvg_manual", "RealSource"]
            ),
            "owner-manual-only": _vote(
                "200", role="owner", weight=5, sources=["bbvg_manual"]
            ),
        },
        "sources": {
            "RealSource": {
                "checks": 123,
                "messages_scanned": 4567,
                "wheel_posts": 8,
                "quality_score": 99,
                "personal_vote_score": 99,
                "personal_vote_points": {"stale": 99},
                "personal_votes": 9,
                "user_votes": 4,
                "admin_votes": 5,
            }
        },
        "daily": {},
    }

    assert reconciliation.reconcile_personal_rating_inventory(stats) is True
    assert stats["personal_wheel_votes"]["user-vote"]["sources"] == ["RealSource"]
    assert stats["personal_wheel_votes"]["owner-manual-only"]["sources"] == []

    source = stats["sources"]["RealSource"]
    assert source["checks"] == 123
    assert source["messages_scanned"] == 4567
    assert source["wheel_posts"] == 8
    assert source["quality_score"] == 1
    assert source["personal_vote_score"] == 1
    assert source["personal_vote_points"] == {"user-vote": 1}
    assert source["personal_votes"] == 1
    assert source["user_votes"] == 1
    assert source["admin_votes"] == 0
    assert reconciliation.reconcile_personal_rating_inventory(stats) is False


def test_repair_consolidates_case_variants_without_double_score(
    tmp_path, monkeypatch
) -> None:
    inventory = tmp_path / "public_sources.txt"
    inventory.write_text("GShikaryan\n", encoding="utf-8")
    monkeypatch.setattr(monitor_data, "PUBLIC_SOURCES_PATH", inventory)

    stats = {
        "version": 1,
        "source_rating_policy": personal_wheel_voting.PERSONAL_RATING_POLICY,
        "personal_wheel_votes": {
            "owner-vote": _vote(
                "300", role="owner", weight=5, sources=["GShikaryan"]
            )
        },
        "sources": {
            "GShikaryan": {"checks": 10, "quality_score": 1},
            "gshikaryan": {"messages_scanned": 20, "quality_score": 4},
        },
        "daily": {},
    }

    assert reconciliation.reconcile_personal_rating_inventory(stats) is True
    total = sum(
        int(entry.get("quality_score", 0) or 0)
        for entry in stats["sources"].values()
    )
    assert total == 5
    assert stats["sources"]["GShikaryan"]["quality_score"] == 5
    assert "quality_score" not in stats["sources"]["gshikaryan"]
    assert stats["sources"]["GShikaryan"]["checks"] == 10
    assert stats["sources"]["gshikaryan"]["messages_scanned"] == 20
    assert reconciliation.reconcile_personal_rating_inventory(stats) is False


def test_repair_is_safe_when_inventory_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(monitor_data, "PUBLIC_SOURCES_PATH", tmp_path / "missing.txt")
    stats = {
        "source_rating_policy": personal_wheel_voting.PERSONAL_RATING_POLICY,
        "personal_wheel_votes": {
            "vote": _vote("400", role="user", weight=1, sources=["bbvg_manual"])
        },
        "sources": {"bbvg_manual": {"quality_score": 1}},
    }
    before = repr(stats)
    assert reconciliation.reconcile_personal_rating_inventory(stats) is False
    assert repr(stats) == before
