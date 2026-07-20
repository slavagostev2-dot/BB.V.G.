from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
LIFECYCLE_STATES = frozenset(
    {"candidate", "observed", "recommended", "approved", "rejected"}
)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def observation_days(entry: dict[str, Any], *, current: datetime | None = None) -> int:
    first = _parse_time(entry.get("first_discovered_at"))
    if first is None:
        return 0
    now = (current or datetime.now(UTC)).astimezone(UTC)
    return max(0, int((now - first).total_seconds() // 86400))


def deterministic_lifecycle(
    entry: dict[str, Any],
    *,
    known: bool = False,
    ignored: bool = False,
) -> tuple[str, str]:
    if known:
        return "approved", "источник уже находится в утверждённом мониторинге"
    if ignored:
        return "rejected", "источник исключён администратором"

    public = entry.get("public") is True
    status = str(entry.get("status") or "unknown").casefold()
    relevant = str(entry.get("relevance_status") or "").casefold() == "relevant"
    score = max(0, int(entry.get("score", 0) or 0))
    wheels = max(0, int(entry.get("wheel_links_found", 0) or 0))
    references = len(entry.get("discovered_from", []) or [])
    mentions = max(0, int(entry.get("mention_count", 0) or 0))

    if not public or status in {"error", "empty", "bot"}:
        return "candidate", "кандидат ещё не прошёл подтверждение публичного канала"
    if wheels >= 2:
        return "recommended", f"в публичном канале найдено прямых ссылок на колёса: {wheels}"
    if wheels >= 1 and score >= 60:
        return "recommended", f"найдена ссылка на колесо и общий рейтинг кандидата {score}/100"
    if wheels >= 1 and (references >= 2 or mentions >= 2):
        return "recommended", "найдена ссылка на колесо и независимые связи с известными источниками"
    if relevant or score >= 35:
        return "observed", "канал подтверждён и имеет тематические признаки, требуется накопить доказательства"
    return "candidate", "доказательств полезности пока недостаточно"


def evaluate_candidate(
    entry: dict[str, Any],
    *,
    known: bool = False,
    ignored: bool = False,
    run_marker: str = "",
) -> bool:
    before = dict(entry)
    lifecycle, reason = deterministic_lifecycle(entry, known=known, ignored=ignored)
    previous = str(entry.get("lifecycle_status") or "")
    entry["lifecycle_status"] = lifecycle
    entry["lifecycle_reason"] = reason
    entry["observation_days"] = observation_days(entry)

    if run_marker and entry.get("last_lifecycle_run") != run_marker:
        entry["observation_runs"] = int(entry.get("observation_runs", 0) or 0) + 1
        entry["last_lifecycle_run"] = run_marker

    if lifecycle == "recommended" and previous != "recommended":
        entry["recommended_at"] = datetime.now(UTC).isoformat()
        entry.pop("recommendation_alerted_at", None)
    return entry != before


def evaluate_state(module: Any, state: dict[str, Any]) -> int:
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    _, known_sources = module.known_sources()
    ignored_sources = module.ignored_sources()
    run_marker = str(state.get("last_run_at") or "")
    changed = 0

    for key, raw in candidates.items():
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or key).strip().lstrip("@")
        if evaluate_candidate(
            raw,
            known=source.casefold() in known_sources,
            ignored=source.casefold() in ignored_sources,
            run_marker=run_marker,
        ):
            changed += 1

    counts = {name: 0 for name in LIFECYCLE_STATES}
    for raw in candidates.values():
        if not isinstance(raw, dict):
            continue
        lifecycle = str(raw.get("lifecycle_status") or "candidate")
        if lifecycle in counts:
            counts[lifecycle] += 1
    summary = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "run_marker": run_marker,
        "counts": counts,
        "recommended": counts["recommended"],
    }
    if state.get("source_discovery_lifecycle") != summary:
        state["source_discovery_lifecycle"] = summary
        changed += 1
    return changed
