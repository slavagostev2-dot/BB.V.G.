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
    """Return public wheel-bearing sources for retention-focused callers/tests.

    Alert delivery has a richer selector in source_intelligence_alerts that also
    applies ignored-source and 24-hour reminder policy. Retention must never
    replace that selector at runtime.
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


def install(module: Any, alerts: Any | None = None) -> None:
    """Keep broad discovery ephemeral while preserving the alert policy layer."""

    if getattr(module, "_bbvg_wheel_only_retention_installed", False):
        return

    original_save = module.save_state

    def save_wheel_only_state(state: dict[str, Any]) -> None:
        removed = prune_to_wheel_sources(state)
        if removed:
            print(f"Source intelligence retention removed speculative candidates: {removed}")
        original_save(state)

    module.save_state = save_wheel_only_state
    # Do not monkeypatch alerts.wheel_candidate_rows here. The alert module's
    # selector owns ignored-source filtering and 24-hour reminder cadence. The
    # former override used this module's narrower two-argument selector and
    # caused production source_intelligence_entry.py to fail after a successful
    # scan when notify_new_candidates passed (state, known, ignored).
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

    class Module:
        _bbvg_wheel_only_retention_installed = False

        def __init__(self) -> None:
            self.saved: dict[str, Any] | None = None

        def save_state(self, value: dict[str, Any]) -> None:
            self.saved = value

    class Alerts:
        @staticmethod
        def wheel_candidate_rows(
            value: dict[str, Any],
            known: set[str] | None = None,
            ignored: set[str] | None = None,
        ) -> list[tuple[str, dict[str, Any]]]:
            return []

    module = Module()
    selector = Alerts.wheel_candidate_rows
    install(module, Alerts)
    assert Alerts.wheel_candidate_rows is selector
    module.save_state(
        {
            "candidates": {
                "maybe": {"source": "Maybe", "wheel_links_found": 0, "public": True},
                "wheel": {"source": "Wheel", "wheel_links_found": 1, "public": True},
            }
        }
    )
    assert module.saved is not None
    assert set(module.saved["candidates"]) == {"wheel"}
    print("source intelligence wheel-only retention self-test passed")


if __name__ == "__main__":
    self_test()
