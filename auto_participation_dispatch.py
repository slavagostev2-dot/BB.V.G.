from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests

from bbvg.storage import EventStore


WORKFLOW_FILE = "auto-participation.yml"
TIMEOUT_SECONDS = 20


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BB-VG-auto-participation-dispatch",
    }


def _workflow_urls(repository: str) -> tuple[str, str]:
    base = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        f"{WORKFLOW_FILE}"
    )
    return f"{base}/dispatches", f"{base}/enable"


def _workflow_disabled(response: requests.Response) -> bool:
    if response.status_code != 422:
        return False
    detail = str(response.text or "").casefold()
    return "disabled workflow" in detail or (
        "cannot trigger" in detail and "disabled" in detail
    )


def _dispatch_with_recovery(
    token: str,
    repository: str,
    branch: str,
    event_payload: dict[str, Any],
) -> tuple[requests.Response, bool, str]:
    dispatch_url, enable_url = _workflow_urls(repository)
    headers = _github_headers(token)
    body = {
        "ref": branch,
        "inputs": {
            "event_payload": json.dumps(
                event_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        },
    }
    response = requests.post(
        dispatch_url,
        headers=headers,
        json=body,
        timeout=TIMEOUT_SECONDS,
    )
    if not _workflow_disabled(response):
        return response, False, ""

    enable_response = requests.put(
        enable_url,
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    if enable_response.status_code != 204:
        return (
            response,
            False,
            "workflow_enable_failed:"
            f"http_{enable_response.status_code}:{enable_response.text[:300]}",
        )
    response = requests.post(
        dispatch_url,
        headers=headers,
        json=body,
        timeout=TIMEOUT_SECONDS,
    )
    return response, True, ""


def dispatch_pending(
    store: EventStore,
    *,
    token: str,
    repository: str,
    branch: str,
    limit: int = 20,
) -> dict[str, int]:
    summary = {"claimed": 0, "dispatched": 0, "retry": 0}
    claimed = store.claim_outbox({"auto_participation"}, limit=limit)
    summary["claimed"] = len(claimed)
    for row in claimed:
        outbox_id = str(row["outbox_id"])
        claim_token = str(row["claim_token"])
        event_id = str(row["event_id"])
        payload = dict(row.get("payload") or {})
        payload["event_id"] = event_id
        try:
            response, reenabled, recovery_error = _dispatch_with_recovery(
                token,
                repository,
                branch,
                payload,
            )
            if response.status_code != 204:
                detail = recovery_error or (
                    f"http_{response.status_code}:{response.text[:500]}"
                )
                raise RuntimeError(detail)
        except Exception as exc:
            store.fail_outbox(
                outbox_id,
                claim_token,
                f"{type(exc).__name__}: {exc}",
                retry_after_seconds=min(300, 15 * max(1, int(row["attempts"]))),
            )
            store.record_transition(
                event_id,
                "dispatch_retry_scheduled",
                payload={
                    "attempt": int(row["attempts"]),
                    "error_type": type(exc).__name__,
                },
                dedupe_key=f"attempt:{int(row['attempts'])}",
            )
            summary["retry"] += 1
            continue

        store.complete_outbox(outbox_id, claim_token)
        store.record_transition(
            event_id,
            "workflow_dispatched",
            payload={
                "repository": repository,
                "ref": branch,
                "workflow": WORKFLOW_FILE,
                "workflow_reenabled": reenabled,
                "attempt": int(row["attempts"]),
            },
            dedupe_key=f"attempt:{int(row['attempts'])}",
        )
        summary["dispatched"] += 1
    return summary


def main() -> int:
    """Drain the durable queue without reading or writing GitHub state.json."""

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = (
        os.getenv("BBVG_DEPLOYMENT_SHA", "").strip()
        or os.getenv("GITHUB_SHA", "").strip()
        or os.getenv("GITHUB_BRANCH", "main").strip()
        or "main"
    )
    store = EventStore()
    if not token or not repository:
        print(
            json.dumps(
                {
                    "status": "credentials_missing",
                    "queue": store.health(),
                },
                sort_keys=True,
            )
        )
        return 0
    summary = dispatch_pending(
        store,
        token=token,
        repository=repository,
        branch=branch,
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["retry"] else 0


def self_test() -> None:
    source = open(__file__, encoding="utf-8").read()
    assert "contents/" + "state.json" not in source
    assert "git " + "push" not in source
    assert "_push_state_" + "before_dispatch" not in source
    assert '"event_payload"' in source
    assert 'os.getenv("GITHUB_SHA"' in source
    print("durable outbox auto participation dispatcher self-test passed")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        raise SystemExit(main())
