from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from .event_store import EventStore, SUCCESS_STATUSES, status_confidence


TIMEOUT_SECONDS = 20
DEFAULT_LEDGER_BRANCH = "runtime-ledger"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BB-VG-event-ledger-sync",
    }


def _record_time(record: dict[str, Any]) -> str:
    return max(
        (
            str(value)
            for key, value in record.items()
            if value and (key.endswith("_at") or key in {"recorded_at", "updated_at"})
        ),
        default="",
    )


def _result_winner(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_success = str(left.get("status") or "").casefold() in SUCCESS_STATUSES
    right_success = str(right.get("status") or "").casefold() in SUCCESS_STATUSES
    if left_success != right_success:
        return dict(left if left_success else right)
    left_score = status_confidence(left.get("status"), left.get("confirmation"))
    right_score = status_confidence(right.get("status"), right.get("confirmation"))
    if left_score != right_score:
        return dict(left if left_score > right_score else right)
    return dict(right if _record_time(right) >= _record_time(left) else left)


def merge_event_snapshots(
    remote: dict[str, Any],
    local: dict[str, Any],
) -> dict[str, Any]:
    """Semantic CRDT-like merge for one immutable event generation."""

    if not remote:
        return dict(local)
    if str(remote.get("event_id") or "") != str(local.get("event_id") or ""):
        raise ValueError("cannot merge different canonical events")
    result = dict(remote)
    earliest_fields = {
        "discovered_at",
        "persisted_at",
        "created_at",
        "api_confirmed_at",
        "dispatch_queued_at",
        "workflow_started_at",
        "browser_started_at",
        "notification_sent_at",
        "closed_at",
        "final_sent_at",
    }
    for key, value in local.items():
        if value in (None, "", [], {}):
            continue
        if key in earliest_fields and result.get(key):
            result[key] = min(str(result[key]), str(value))
        elif key not in {
            "aliases",
            "transitions",
            "account_results",
            "account_attempts",
            "notifications",
        }:
            result[key] = value
    result["aliases"] = sorted(
        {
            str(value)
            for value in [*remote.get("aliases", []), *local.get("aliases", [])]
            if str(value)
        }
    )
    for field, identity in (
        ("transitions", "transition_id"),
        ("account_attempts", "attempt_id"),
        ("notifications", "delivery_id"),
    ):
        merged = {
            str(row.get(identity)): dict(row)
            for row in remote.get(field, [])
            if isinstance(row, dict) and row.get(identity)
        }
        for row in local.get(field, []):
            if not isinstance(row, dict) or not row.get(identity):
                continue
            key = str(row[identity])
            previous = merged.get(key)
            if (
                field == "notifications"
                and isinstance(previous, dict)
                and (previous.get("sent_at") or previous.get("telegram_message_id"))
            ):
                continue
            merged[key] = dict(row)
        result[field] = sorted(merged.values(), key=lambda row: str(row.get(identity)))
    account_results: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*remote.get("account_results", []), *local.get("account_results", [])]:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("owner_id") or ""), str(row.get("account_key") or ""))
        if not all(key):
            continue
        previous = account_results.get(key)
        account_results[key] = (
            _result_winner(previous, row) if previous is not None else dict(row)
        )
    result["account_results"] = [
        account_results[key] for key in sorted(account_results)
    ]
    result["updated_at"] = max(
        str(remote.get("updated_at") or ""),
        str(local.get("updated_at") or ""),
    )
    return result


class GitHubLedgerSync:
    def __init__(
        self,
        token: str,
        repository: str,
        *,
        branch: str = DEFAULT_LEDGER_BRANCH,
        source_branch: str = "main",
    ) -> None:
        self.token = token
        self.repository = repository
        self.branch = branch
        self.source_branch = source_branch
        self.api = f"https://api.github.com/repos/{repository}"

    def ensure_branch(self) -> None:
        headers = _headers(self.token)
        ref_url = f"{self.api}/git/ref/heads/{quote(self.branch, safe='')}"
        response = requests.get(ref_url, headers=headers, timeout=TIMEOUT_SECONDS)
        if response.status_code == 200:
            return
        if response.status_code != 404:
            response.raise_for_status()
        source = requests.get(
            f"{self.api}/git/ref/heads/{quote(self.source_branch, safe='')}",
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        source.raise_for_status()
        sha = str(source.json()["object"]["sha"])
        created = requests.post(
            f"{self.api}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{self.branch}", "sha": sha},
            timeout=TIMEOUT_SECONDS,
        )
        if created.status_code not in {201, 422}:
            created.raise_for_status()

    @staticmethod
    def snapshot_path(snapshot: dict[str, Any]) -> str:
        anchor = str(
            snapshot.get("server_start_at")
            or snapshot.get("discovered_at")
            or ""
        )[:10]
        try:
            datetime.fromisoformat(anchor)
        except ValueError:
            anchor = "unknown-date"
        generation = str(
            snapshot.get("generation_id")
            or snapshot.get("event_id")
            or "unknown"
        ).replace(":", "_")
        return f"events/{anchor}/{generation}.json"

    def sync_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.ensure_branch()
        path = self.snapshot_path(snapshot)
        url = f"{self.api}/contents/{quote(path, safe='/')}"
        headers = _headers(self.token)
        last_error = ""
        for attempt in range(1, 6):
            response = requests.get(
                url,
                headers=headers,
                params={"ref": self.branch},
                timeout=TIMEOUT_SECONDS,
            )
            sha = ""
            remote: dict[str, Any] = {}
            if response.status_code == 200:
                body = response.json()
                sha = str(body.get("sha") or "")
                remote = json.loads(
                    base64.b64decode(str(body.get("content") or "")).decode("utf-8")
                )
            elif response.status_code != 404:
                response.raise_for_status()
            merged = merge_event_snapshots(remote, snapshot)
            payload: dict[str, Any] = {
                "message": f"Sync event ledger {snapshot['event_id']} [skip ci]",
                "branch": self.branch,
                "content": base64.b64encode(
                    (
                        json.dumps(
                            merged,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                ).decode("ascii"),
            }
            if sha:
                payload["sha"] = sha
            updated = requests.put(
                url,
                headers=headers,
                json=payload,
                timeout=TIMEOUT_SECONDS,
            )
            if updated.status_code in {200, 201}:
                return
            if updated.status_code in {409, 422}:
                last_error = f"http_{updated.status_code}:{updated.text[:300]}"
                time.sleep(0.25 * attempt)
                continue
            updated.raise_for_status()
        raise RuntimeError(last_error or "event ledger CAS exhausted")


def sync_pending(
    store: EventStore,
    *,
    token: str,
    repository: str,
    branch: str = DEFAULT_LEDGER_BRANCH,
    limit: int = 50,
) -> dict[str, int]:
    client = GitHubLedgerSync(token, repository, branch=branch)
    rows = store.claim_outbox({"github_ledger_sync"}, limit=limit)
    summary = {"claimed": len(rows), "synced": 0, "retry": 0}
    for row in rows:
        try:
            client.sync_snapshot(store.event_snapshot(str(row["event_id"])))
        except Exception as exc:
            store.fail_outbox(
                str(row["outbox_id"]),
                str(row["claim_token"]),
                f"{type(exc).__name__}: {exc}",
                retry_after_seconds=min(600, 20 * max(1, int(row["attempts"]))),
            )
            summary["retry"] += 1
        else:
            store.complete_outbox(
                str(row["outbox_id"]),
                str(row["claim_token"]),
            )
            summary["synced"] += 1
    return summary


def sync_from_environment(store: EventStore) -> dict[str, Any]:
    token = (
        os.getenv("GITHUB_TOKEN", "").strip()
        or os.getenv("GH_TOKEN", "").strip()
    )
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        return {"status": "credentials_missing", **store.health()}
    return {
        "status": "ok",
        **sync_pending(
            store,
            token=token,
            repository=repository,
            branch=os.getenv(
                "BBVG_LEDGER_BRANCH",
                DEFAULT_LEDGER_BRANCH,
            ).strip()
            or DEFAULT_LEDGER_BRANCH,
        ),
    }
