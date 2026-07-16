from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Sequence

DEFAULT_STATS_PATH = Path(__file__).resolve().parent / "source_stats.json"


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

    ``inactive`` must reverse an earlier confirmation for the same wheel. The
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


def load_rating_data(path: Path) -> dict[str, Any]:
    """Load rating state, returning the stable empty shape for invalid input."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {"version": 1, "sources": {}, "daily": {}}
    if not isinstance(value, dict):
        return {"version": 1, "sources": {}, "daily": {}}
    return value


def normalize_file(path: Path) -> bool:
    """Normalize one rating JSON file and replace it atomically when changed."""
    data = load_rating_data(path)
    changed = normalize_additive_rating(data)
    if changed:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return changed


def self_test() -> None:
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "source_stats.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": {
                        "first": {
                            "quality_score": -80,
                            "quality_decisions": {
                                "wheel-a": -40,
                                "wheel-b": 40,
                            },
                        },
                        "second": {"quality_score": -5},
                    },
                    "daily": {},
                }
            ),
            encoding="utf-8",
        )
        assert normalize_file(path) is True
        normalized = json.loads(path.read_text(encoding="utf-8"))
        assert normalized["source_rating_policy"] == "additive_only_v1"
        assert normalized["sources"]["first"]["quality_decisions"] == {
            "wheel-a": 0,
            "wheel-b": 40,
        }
        assert normalized["sources"]["first"]["quality_score"] == 40
        assert normalized["sources"]["second"]["quality_score"] == 0
        assert normalize_file(path) is False
    print("rating policy file normalization self-test passed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize BB V.G. source ratings")
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_STATS_PATH,
        help="Path to source_stats.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    changed = normalize_file(args.path)
    print(f"Additive source rating normalization: {'changed' if changed else 'unchanged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
