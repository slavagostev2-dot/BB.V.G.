from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bbvg.monitor import source_discovery

UTC = timezone.utc


def _entry(**values):
    base = {
        "source": "Candidate",
        "public": True,
        "status": "ok",
        "relevance_status": "relevant",
        "score": 50,
        "wheel_links_found": 0,
        "mention_count": 1,
        "discovered_from": ["origin"],
        "first_discovered_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
    }
    base.update(values)
    return base


def test_candidate_lifecycle_requires_evidence() -> None:
    status, _ = source_discovery.deterministic_lifecycle(
        _entry(public=False, status="unknown")
    )
    assert status == "candidate"

    status, _ = source_discovery.deterministic_lifecycle(_entry())
    assert status == "observed"


def test_direct_wheel_evidence_recommends_candidate() -> None:
    status, reason = source_discovery.deterministic_lifecycle(
        _entry(wheel_links_found=2, score=80)
    )
    assert status == "recommended"
    assert "2" in reason


def test_single_wheel_plus_independent_connections_recommends() -> None:
    status, _ = source_discovery.deterministic_lifecycle(
        _entry(
            wheel_links_found=1,
            score=45,
            mention_count=2,
            discovered_from=["one", "two"],
        )
    )
    assert status == "recommended"


def test_known_and_ignored_are_terminal_admin_states() -> None:
    assert source_discovery.deterministic_lifecycle(_entry(), known=True)[0] == "approved"
    assert source_discovery.deterministic_lifecycle(_entry(), ignored=True)[0] == "rejected"


def test_recommendation_transition_resets_alert_marker() -> None:
    entry = _entry(
        lifecycle_status="observed",
        recommendation_alerted_at="old",
        wheel_links_found=2,
    )
    changed = source_discovery.evaluate_candidate(entry, run_marker="run-1")
    assert changed is True
    assert entry["lifecycle_status"] == "recommended"
    assert "recommendation_alerted_at" not in entry
    assert entry["recommended_at"]
    assert entry["observation_runs"] == 1


def test_same_run_does_not_increment_observation_twice() -> None:
    entry = _entry()
    source_discovery.evaluate_candidate(entry, run_marker="same-run")
    source_discovery.evaluate_candidate(entry, run_marker="same-run")
    assert entry["observation_runs"] == 1
