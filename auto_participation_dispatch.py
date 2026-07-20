from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests


STATE_PATH = Path(__file__).with_name("state.json")
_PENDING_STATUSES = {
    "workflow_dispatch_scheduled",
    "workflow_dispatch_retry_scheduled",
}


def _load_state() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    """Send queued workflow_dispatch requests after monitor state is persisted."""

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
    if not token or not repository:
        print("Auto participation dispatch skipped: GitHub runtime credentials are missing")
        return 0

    state = _load_state()
    dispatch_events = state.get("auto_participation_dispatch_events")
    if not isinstance(dispatch_events, dict):
        print("Auto participation dispatch skipped: no dispatch ledger")
        return 0

    pending = {
        token_key: entry
        for token_key, entry in dispatch_events.items()
        if isinstance(entry, dict) and str(entry.get("status") or "") in _PENDING_STATUSES
    }
    if not pending:
        print("Auto participation dispatch skipped: no queued events")
        return 0

    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        "auto-participation.yml/dispatches"
    )
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": branch},
            timeout=20,
        )
    except requests.RequestException as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"Auto participation dispatch request failed: {detail}")
        now = datetime.now(timezone.utc).isoformat()
        for entry in pending.values():
            entry["dispatch_failed_at"] = now
            entry["dispatch_error"] = detail[:500]
        _save_state(state)
        return 1

    now = datetime.now(timezone.utc).isoformat()
    if response.status_code == 204:
        for entry in pending.values():
            entry["status"] = "workflow_dispatch_sent"
            entry["dispatched_at"] = now
            entry.pop("dispatch_error", None)
            entry.pop("dispatch_failed_at", None)
        _save_state(state)
        print(
            "Auto participation workflow dispatched: "
            f"events={len(pending)} repository={repository} ref={branch} "
            "workflow=auto-participation.yml"
        )
        return 0

    detail = f"HTTP {response.status_code} {response.text[:500]}"
    for entry in pending.values():
        entry["dispatch_failed_at"] = now
        entry["dispatch_error"] = detail
    _save_state(state)
    print(
        "Auto participation dispatch failed: "
        f"repository={repository} ref={branch} workflow=auto-participation.yml {detail}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
