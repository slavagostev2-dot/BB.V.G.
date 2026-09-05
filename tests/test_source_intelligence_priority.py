from __future__ import annotations

import source_intelligence


def _candidate(source: str, mentions: int, references: int) -> dict[str, object]:
    return {
        "source": source,
        "mention_count": mentions,
        "discovered_from": {f"source{index}" for index in range(references)},
    }


def test_previous_wheel_source_has_verification_priority() -> None:
    proven = _candidate("provenwheel", 1, 1)
    popular = _candidate("popularcandidate", 50, 8)
    previous = {
        "provenwheel": {"wheel_links_found": 1},
        "popularcandidate": {"wheel_links_found": 0},
    }

    ordered = sorted(
        [popular, proven],
        key=lambda item: source_intelligence.verification_priority(item, previous),
    )

    assert [item["source"] for item in ordered] == [
        "provenwheel",
        "popularcandidate",
    ]


def test_priority_preserves_existing_order_inside_same_class() -> None:
    stronger_mentions = _candidate("manymentions", 7, 1)
    stronger_references = _candidate("manyreferences", 3, 4)
    alphabetical = _candidate("alphabetical", 3, 1)

    ordered = sorted(
        [alphabetical, stronger_references, stronger_mentions],
        key=lambda item: source_intelligence.verification_priority(item, {}),
    )

    assert [item["source"] for item in ordered] == [
        "manymentions",
        "manyreferences",
        "alphabetical",
    ]
