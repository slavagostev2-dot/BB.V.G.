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


def wheel_candidate_rows(
    state: dict[str, Any],
    known_sources: set[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return new public sources with direct BetBoom wheel evidence.

    This remains the stable selector used by tests and alerting. It no longer
    exposes speculative discovery candidates because those are pruned before
    persistence.
    """

    known = {str(value).casefold() for value in (known_sources or set())}
    candidates = state.get("candidates")
    if not isinstance(candidates, dict):
        return []
    rows: list[tuple[str, dict[str, Any]]] = []
    for key, raw in candidates.items():
        if not isinstance(raw, dict) or _wheel_count(raw) <= 0:
            continue
        source = str(raw.get("source") or key).strip().lstrip("@")
        if (
            not source
            or source.casefold().endswith("bot")
            or source.casefold() in known
            or raw.get("public") is not True
        ):
            continue
        rows.append((source, raw))
    rows.sort(
        key=lambda item: (
            -_wheel_count(item[1]),
            str(item[1].get("latest_wheel_at") or ""),
            item[0].casefold(),
        )
    )
    return rows


def install(module: Any, alerts: Any) -> None:
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
    alerts.wheel_candidate_rows = wheel_candidate_rows
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
    assert [source for source, _ in wheel_candidate_rows(state)] == ["Wheel"]
    print("source intelligence wheel-only retention self-test passed")


if __name__ == "__main__":
    self_test()
