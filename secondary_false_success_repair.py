from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
SECONDARY_ACCOUNTS = {"vyacheslav_secondary", "xflarxx_primary"}
LEGACY_FALSE_PRECICK_DETAILS = {
    "betboom уже показывает точное подтверждение участия",
    "betboom показывает точное подтверждение после повторной загрузки",
}


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def repair_state(state: dict[str, Any], *, repaired_at: str | None = None) -> int:
    """Make only known legacy false pre-click successes retryable once."""

    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return 0
    stamp = repaired_at or datetime.now(UTC).isoformat()
    repaired = 0
    for raw in events.values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("account_key") or "") not in SECONDARY_ACCOUNTS:
            continue
        if str(raw.get("status") or "").casefold() != "participated":
            continue
        if _normalized(raw.get("detail")) not in LEGACY_FALSE_PRECICK_DETAILS:
            continue

        raw["status"] = "unconfirmed"
        raw["retry_allowed"] = True
        raw.pop("retry_after_at", None)
        raw.pop("bot_success_pending_at", None)
        raw.pop("bot_success_sync_status", None)
        raw.pop("bot_success_sync_version", None)
        raw["legacy_false_success_repaired_at"] = stamp
        raw["legacy_false_success_detail"] = str(raw.get("detail") or "")[:300]
        raw["detail"] = (
            "Старое pre-click подтверждение признано недостаточным; "
            "требуется повторная проверка BetBoom"
        )
        repaired += 1
    return repaired


def repair_file(path: Path) -> int:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать runtime-state: {exc}") from exc
    if not isinstance(state, dict):
        raise RuntimeError("Runtime-state должен быть JSON-объектом")
    repaired = repair_state(state)
    if repaired:
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return repaired


def self_test() -> None:
    state = {
        "auto_participation_events": {
            "old-second": {
                "account_key": "vyacheslav_secondary",
                "status": "participated",
                "detail": "BetBoom уже показывает точное подтверждение участия",
                "bot_success_pending_at": "2026-09-05T10:00:00+00:00",
                "bot_success_sync_status": "waiting_for_control_center",
                "retry_allowed": False,
            },
            "old-third": {
                "account_key": "xflarxx_primary",
                "status": "participated",
                "detail": "BetBoom показывает точное подтверждение после повторной загрузки",
                "retry_allowed": False,
            },
            "new-strict": {
                "account_key": "vyacheslav_secondary",
                "status": "participated",
                "detail": (
                    "BetBoom уже показывает самостоятельный статус участия "
                    "(preclick_exact_success_label)"
                ),
                "retry_allowed": False,
            },
            "primary": {
                "account_key": "vyacheslav_primary",
                "status": "participated",
                "detail": "BetBoom уже показывает точное подтверждение участия",
                "retry_allowed": False,
            },
            "post-click": {
                "account_key": "vyacheslav_secondary",
                "status": "participated",
                "detail": "BetBoom подтвердил участие после нажатия (exact_success_label)",
                "retry_allowed": False,
            },
        }
    }
    repaired = repair_state(state, repaired_at="2026-09-05T14:00:00+00:00")
    assert repaired == 2
    assert state["auto_participation_events"]["old-second"]["status"] == "unconfirmed"
    assert state["auto_participation_events"]["old-second"]["retry_allowed"] is True
    assert "bot_success_pending_at" not in state["auto_participation_events"]["old-second"]
    assert state["auto_participation_events"]["old-third"]["status"] == "unconfirmed"
    assert state["auto_participation_events"]["new-strict"]["status"] == "participated"
    assert state["auto_participation_events"]["primary"]["status"] == "participated"
    assert state["auto_participation_events"]["post-click"]["status"] == "participated"
    print("secondary false participation success repair self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    repaired = repair_file(args.state)
    print(json.dumps({"repaired": repaired}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
