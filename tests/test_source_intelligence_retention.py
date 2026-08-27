from __future__ import annotations

import source_intelligence_alerts as alerts
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


def test_retention_install_preserves_alert_selector_contract() -> None:
    class Module:
        _bbvg_wheel_only_retention_installed = False

        def __init__(self) -> None:
            self.saved: dict | None = None

        def save_state(self, state: dict) -> None:
            self.saved = state

    module = Module()
    original_selector = alerts.wheel_candidate_rows

    retention.install(module, alerts)

    assert alerts.wheel_candidate_rows is original_selector
    state = {
        "candidates": {
            "known": {
                "source": "Known",
                "public": True,
                "wheel_links_found": 1,
            },
            "ignored": {
                "source": "Ignored",
                "public": True,
                "wheel_links_found": 1,
            },
            "new": {
                "source": "NewSource",
                "public": True,
                "wheel_links_found": 1,
            },
        }
    }
    rows = alerts.wheel_candidate_rows(state, {"known"}, {"ignored"})
    assert [source for source, _ in rows] == ["NewSource"]

    module.save_state(
        {
            "candidates": {
                "maybe": {
                    "source": "Maybe",
                    "public": True,
                    "wheel_links_found": 0,
                },
                "wheel": {
                    "source": "Wheel",
                    "public": True,
                    "wheel_links_found": 1,
                },
            }
        }
    )
    assert module.saved is not None
    assert set(module.saved["candidates"]) == {"wheel"}
