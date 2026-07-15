from __future__ import annotations

from typing import Any, Callable


def normalize_additive_rating(data: dict[str, Any]) -> bool:
    """Remove negative rating effects while preserving operational counters."""
    changed = False
    sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
    for entry in sources.values():
        if not isinstance(entry, dict):
            continue
        decisions = entry.get("quality_decisions")
        if isinstance(decisions, dict):
            for wheel_key, raw_points in list(decisions.items()):
                points = max(0, int(raw_points or 0))
                if points != int(raw_points or 0):
                    decisions[wheel_key] = points
                    changed = True
            score = sum(max(0, int(value or 0)) for value in decisions.values())
        else:
            score = max(0, int(entry.get("quality_score", 0) or 0))
        if int(entry.get("quality_score", 0) or 0) != score:
            entry["quality_score"] = score
            changed = True
    if data.get("source_rating_policy") != "additive_only_v1":
        data["source_rating_policy"] = "additive_only_v1"
        changed = True
    return changed


def record_admin_wheel_decision(
    data: dict[str, Any],
    *,
    wheel_key: str,
    sources: list[str],
    decision: str,
    actor: str,
    at: Any,
    recorder: Callable[..., bool],
) -> bool:
    """Apply the latest administrator verdict with a non-negative rating.

    ``inactive`` must reverse an earlier confirmation for the same wheel.  The
    source loses the points from that confirmation and receives an inactive
    counter, but its total score is never allowed to become negative.
    """
    changed = recorder(
        data,
        wheel_key=wheel_key,
        sources=sources,
        decision=decision,
        actor=actor,
        at=at,
    )
    normalized_changed = normalize_additive_rating(data)
    return changed or normalized_changed
