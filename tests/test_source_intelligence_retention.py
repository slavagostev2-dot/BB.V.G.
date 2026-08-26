from __future__ import annotations

import source_intelligence_retention as retention


def test_speculative_candidates_are_not_persisted() -> None:
    state = {
        "candidates": {
            "maybe": {"source": "Maybe", "public": True, "wheel_links_found": 0},
            "wheel": {"source": "Wheel", "public": True, "wheel_links_found": 1},
        },
        "edges": {
            "known->maybe": {"from": "Known", "to": "Maybe"},
            "known->wheel": {"from": "Known", "to": "Wheel"},
        },
    }
    removed = retention.prune_to_wheel_sources(state)
    assert removed == 1
    assert set(state["candidates"]) == {"wheel"}
    assert set(state["edges"]) == {"known->wheel"}


def test_every_new_public_wheel_source_is_actionable() -> None:
    state = {
        "candidates": {
            "low_score": {
                "source": "LowScore",
                "public": True,
                "wheel_links_found": 1,
                "score": 5,
                "lifecycle_status": "observed",
            },
            "no_wheel": {
                "source": "NoWheel",
                "public": True,
                "wheel_links_found": 0,
                "score": 100,
                "lifecycle_status": "recommended",
            },
        }
    }
    assert [source for source, _ in retention.wheel_candidate_rows(state)] == ["LowScore"]
