from __future__ import annotations

from typing import Any


def _wheel_count(entry: Any) -> int:
    if not isinstance(entry, dict):
        return 0
    try:
        return max(0, int(entry.get("wheel_links_found", 0) or 0))
    except (TypeError, ValueError):
        return 0


def prune_to_wheel_sources(state: dict[str, Any]) -> int:
    """Persist only discovered sources that have direct BetBoom wheel evidence."""

    candidates = state.get("candidates")
    if not isinstance(candidates, dict):
        candidates = {}
    kept = {
        str(key): value
        for key, value in candidates.items()
        if isinstance(value, dict) and _wheel_count(value) > 0
    }
    removed = max(0, len(candidates) - len(kept))
    state["candidates"] = kept

    kept_keys = {key.casefold() for key in kept}
    edges = state.get("edges")
    if isinstance(edges, dict):
        state["edges"] = {
            str(key): value
            for key, value in edges.items()
            if isinstance(value, dict)
            and str(value.get("to") or "").strip().lstrip("@").casefold() in kept_keys
        }
    else:
        state["edges"] = {}

    return removed


def install(module: Any, _alerts: Any) -> None:
    """Keep broad discovery ephemeral while persisting only wheel-proven sources."""

    if getattr(module, "_bbvg_wheel_only_retention_installed", False):
        return

    original_save = module.save_state

    def save_wheel_only_state(state: dict[str, Any]) -> None:
        removed = prune_to_wheel_sources(state)
        if removed:
            print(f"Source intelligence retention removed speculative candidates: {removed}")
        original_save(state)

    module.save_state = save_wheel_only_state
    module._bbvg_wheel_only_retention_installed = True


def self_test() -> None:
    state = {
        "candidates": {
            "maybe": {"source": "Maybe", "wheel_links_found": 0, "public": True},
            "wheel": {"source": "Wheel", "wheel_links_found": 1, "public": True},
            "privatewheel": {"source": "PrivateWheel", "wheel_links_found": 2, "public": False},
        },
        "edges": {
            "a->maybe": {"from": "a", "to": "Maybe"},
            "a->wheel": {"from": "a", "to": "Wheel"},
        },
    }
    assert prune_to_wheel_sources(state) == 1
    assert set(state["candidates"]) == {"wheel", "privatewheel"}
    assert set(state["edges"]) == {"a->wheel"}
    print("source intelligence wheel-only retention self-test passed")


if __name__ == "__main__":
    self_test()
