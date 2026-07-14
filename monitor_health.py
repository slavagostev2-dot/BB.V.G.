from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATUS_PATH = ROOT / "monitor_status.json"
STATE_PATH = ROOT / "state.json"
STATS_PATH = ROOT / "source_stats.json"
UTC = timezone.utc
RESTART_FAILURE_THRESHOLD = max(1, int(os.getenv("MONITOR_RESTART_FAILURES", "2")))
RESTART_NO_PROGRESS_THRESHOLD = max(
    1, int(os.getenv("MONITOR_RESTART_NO_PROGRESS", "2"))
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default or {})
    return value if isinstance(value, dict) else dict(default or {})


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def runtime_snapshot() -> dict[str, int]:
    state = load_json(STATE_PATH)
    stats = load_json(STATS_PATH)
    summary = (
        state.get("last_run_summary")
        if isinstance(state.get("last_run_summary"), dict)
        else {}
    )
    sources = stats.get("sources") if isinstance(stats.get("sources"), dict) else {}
    checks_total = sum(
        int(entry.get("checks", 0) or 0)
        for entry in sources.values()
        if isinstance(entry, dict)
    )
    return {
        "checked_sources": int(summary.get("checked_sources", 0) or 0),
        "reachable_sources": int(summary.get("reachable_sources", 0) or 0),
        "source_errors": int(summary.get("source_errors", 0) or 0),
        "checks_total": checks_total,
        "pending_wheels": len(state.get("pending_posts", {}))
        if isinstance(state.get("pending_posts"), dict)
        else 0,
    }


def should_restart(consecutive_failures: int, consecutive_no_progress: int) -> bool:
    return (
        consecutive_failures >= RESTART_FAILURE_THRESHOLD
        or consecutive_no_progress >= RESTART_NO_PROGRESS_THRESHOLD
    )


def start_run(run_id: str) -> dict[str, Any]:
    current = load_json(STATUS_PATH)
    timestamp = now_utc().isoformat()
    current.update(
        {
            "version": 3,
            "brand": "BB V.G.",
            "status": "starting",
            "run_id": str(run_id or ""),
            "run_started_at": timestamp,
            "last_process_heartbeat_at": timestamp,
            "restart_recommended": False,
        }
    )
    save_json(STATUS_PATH, current)
    return current


def record_iteration(
    *,
    run_id: str,
    iteration: int,
    exit_code: int,
    duration_seconds: int,
) -> dict[str, Any]:
    previous = load_json(STATUS_PATH)
    snapshot = runtime_snapshot()
    current_time = now_utc()
    previous_checks = int(previous.get("checks_total", 0) or 0)
    checks_total = snapshot["checks_total"]
    counter_reset = checks_total < previous_checks
    checks_delta = checks_total - previous_checks if not counter_reset else checks_total
    process_ok = exit_code == 0
    made_progress = checks_delta > 0 and snapshot["checked_sources"] > 0
    healthy = process_ok and made_progress

    consecutive_failures = (
        0
        if process_ok
        else int(previous.get("consecutive_failures", 0) or 0) + 1
    )
    consecutive_no_progress = (
        0
        if made_progress
        else int(previous.get("consecutive_no_progress", 0) or 0) + 1
    )
    restart_needed = should_restart(consecutive_failures, consecutive_no_progress)

    if not process_ok:
        status = (
            "iteration_timeout"
            if exit_code in {124, 137, 143}
            else "iteration_error"
        )
        reason = f"monitor process exit code {exit_code}"
    elif not made_progress:
        status = "no_progress"
        reason = "iteration finished but source check counters did not advance"
    else:
        status = "running"
        reason = ""

    payload: dict[str, Any] = {
        "version": 3,
        "brand": "BB V.G.",
        "status": status,
        "run_id": str(run_id or ""),
        "iteration": int(iteration),
        "last_iteration_at": current_time.isoformat(),
        "last_process_heartbeat_at": current_time.isoformat(),
        "last_iteration_exit_code": int(exit_code),
        "last_iteration_duration_seconds": max(0, int(duration_seconds)),
        "checks_delta": int(checks_delta),
        "stats_counter_reset": bool(counter_reset),
        "consecutive_failures": consecutive_failures,
        "consecutive_no_progress": consecutive_no_progress,
        "restart_recommended": restart_needed,
        **snapshot,
    }

    for key in (
        "run_started_at",
        "last_successful_iteration_at",
        "last_successful_checks_total",
        "last_failure_at",
        "last_failure_reason",
    ):
        if key in previous:
            payload[key] = previous[key]

    if healthy:
        payload["last_successful_iteration_at"] = current_time.isoformat()
        payload["last_successful_checks_total"] = checks_total
        payload.pop("last_failure_reason", None)
    else:
        payload["last_failure_at"] = current_time.isoformat()
        payload["last_failure_reason"] = reason

    save_json(STATUS_PATH, payload)
    return payload


def health_check(
    *,
    max_age_minutes: int,
    max_success_age_minutes: int,
    max_consecutive_failures: int,
    max_consecutive_no_progress: int,
) -> tuple[bool, str, dict[str, Any]]:
    status = load_json(STATUS_PATH)
    now = now_utc()
    reasons: list[str] = []

    last_iteration = parse_datetime(status.get("last_iteration_at"))
    last_success = parse_datetime(status.get("last_successful_iteration_at"))
    if last_iteration is None:
        reasons.append("no completed iteration recorded")
    elif now - last_iteration > timedelta(minutes=max_age_minutes):
        age = int((now - last_iteration).total_seconds() // 60)
        reasons.append(f"last iteration is {age} minutes old")

    if last_success is None:
        reasons.append("no successful progressing iteration recorded")
    elif now - last_success > timedelta(minutes=max_success_age_minutes):
        age = int((now - last_success).total_seconds() // 60)
        reasons.append(f"last successful iteration is {age} minutes old")

    failures = int(status.get("consecutive_failures", 0) or 0)
    no_progress = int(status.get("consecutive_no_progress", 0) or 0)
    if failures >= max_consecutive_failures:
        reasons.append(f"{failures} consecutive process failures")
    if no_progress >= max_consecutive_no_progress:
        reasons.append(f"{no_progress} consecutive iterations without source progress")

    stale = bool(reasons)
    return stale, "; ".join(reasons) if reasons else "runtime is healthy", status


def write_github_output(values: dict[str, object]) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as stream:
        for key, value in values.items():
            text = str(value).replace("\n", " ")
            stream.write(f"{key}={text}\n")


def self_test() -> None:
    assert parse_datetime("2026-07-14T00:00:00Z") is not None
    assert parse_datetime("bad") is None
    assert not should_restart(0, 0)
    assert should_restart(RESTART_FAILURE_THRESHOLD, 0)
    assert should_restart(0, RESTART_NO_PROGRESS_THRESHOLD)
    print("monitor_health self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start")
    start.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", ""))

    record = subparsers.add_parser("record")
    record.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", ""))
    record.add_argument("--iteration", type=int, required=True)
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--duration-seconds", type=int, default=0)

    check = subparsers.add_parser("check")
    check.add_argument("--max-age-minutes", type=int, default=30)
    check.add_argument("--max-success-age-minutes", type=int, default=30)
    check.add_argument("--max-consecutive-failures", type=int, default=2)
    check.add_argument("--max-consecutive-no-progress", type=int, default=2)

    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.command == "start":
        payload = start_run(args.run_id)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "record":
        payload = record_iteration(
            run_id=args.run_id,
            iteration=args.iteration,
            exit_code=args.exit_code,
            duration_seconds=args.duration_seconds,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "check":
        stale, reason, status = health_check(
            max_age_minutes=max(1, args.max_age_minutes),
            max_success_age_minutes=max(1, args.max_success_age_minutes),
            max_consecutive_failures=max(1, args.max_consecutive_failures),
            max_consecutive_no_progress=max(1, args.max_consecutive_no_progress),
        )
        write_github_output(
            {
                "stale": "true" if stale else "false",
                "reason": reason,
                "status": status.get("status", "unknown"),
                "run_id": status.get("run_id", ""),
                "checks_total": status.get("checks_total", 0),
                "restart_recommended": str(
                    bool(status.get("restart_recommended", False))
                ).lower(),
            }
        )
        print(reason)
        return 0
    parser.error("a command is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
