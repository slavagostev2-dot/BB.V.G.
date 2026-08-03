from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

import auto_participation_bot_sync
from bbvg.storage import event_id_from_entry
from runtime_outbox_reconciliation import (
    reconcile_runtime_outbox,
    refresh_functional_health,
)

GITHUB_API_VERSION = "2022-11-28"
RUNTIME_STATE_BRANCH = "runtime-state"
PUBLISH_ATTEMPTS = 4
PUBLISH_TIMEOUT_SECONDS = 20

_SEMANTIC_COLLECTIONS = (
    "auto_participation_events",
    "auto_participation_dispatch_events",
    "auto_participation_attempts",
    "button_contexts",
    "participating_wheels",
    "wheel_publications",
)
_ACCOUNT_REGISTRY = "auto_participation_account_registry"
_EVENT_MODE_MARKER = "auto_participation_event_mode_initialized_at"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid local JSON {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid local JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "BB-VG-monitor-runtime-publisher",
    }


def _event_token(item: Any, key: str) -> str:
    if not isinstance(item, dict):
        return ""
    return event_id_from_entry(item, wheel_key=key)


def _merge_registry(local: Any, remote: Any) -> dict[str, Any]:
    result = copy.deepcopy(local) if isinstance(local, dict) else {}
    if not isinstance(remote, dict):
        return result
    for raw_key, raw_value in remote.items():
        key = str(raw_key)
        if key not in result:
            result[key] = copy.deepcopy(raw_value)
            continue
        if isinstance(result.get(key), dict) and isinstance(raw_value, dict):
            combined = copy.deepcopy(result[key])
            combined.update(copy.deepcopy(raw_value))
            result[key] = combined
    return result


def _merge_active_wheels(local: Any, remote: Any) -> dict[str, Any]:
    """Preserve Monitor lifecycle while retaining same-event browser outcomes."""

    local_rows = local if isinstance(local, dict) else {}
    remote_rows = remote if isinstance(remote, dict) else {}
    remote_by_key = {str(key).casefold(): value for key, value in remote_rows.items()}
    result = copy.deepcopy(local_rows)

    for raw_key, local_item in list(result.items()):
        key = str(raw_key).casefold()
        remote_item = remote_by_key.get(key)
        if not isinstance(local_item, dict) or not isinstance(remote_item, dict):
            continue
        if _event_token(local_item, key) != _event_token(remote_item, key):
            continue
        semantic = auto_participation_bot_sync.merge_auto_participation_state(
            {"active_wheels": {key: local_item}},
            {"active_wheels": {key: remote_item}},
        )
        active = semantic.get("active_wheels")
        if isinstance(active, dict) and isinstance(active.get(key), dict):
            result[raw_key] = active[key]

    return result


def merge_monitor_runtime_state(
    local_monitor_state: dict[str, Any],
    latest_remote_state: dict[str, Any],
) -> dict[str, Any]:
    """Merge concurrent browser-owned outcomes into Monitor-owned state.

    The Monitor remains authoritative for source discovery and lifecycle, including
    removal of closed wheels. Browser workflows may only contribute their durable
    result collections and same-event participation fields.
    """

    local = local_monitor_state if isinstance(local_monitor_state, dict) else {}
    remote = latest_remote_state if isinstance(latest_remote_state, dict) else {}
    merged = copy.deepcopy(local)

    local_semantic = {
        key: copy.deepcopy(local[key])
        for key in _SEMANTIC_COLLECTIONS
        if key in local
    }
    remote_semantic = {
        key: copy.deepcopy(remote[key])
        for key in _SEMANTIC_COLLECTIONS
        if key in remote
    }
    semantic = auto_participation_bot_sync.merge_auto_participation_state(
        local_semantic,
        remote_semantic,
    )
    for key in _SEMANTIC_COLLECTIONS:
        if key in semantic:
            merged[key] = semantic[key]
        elif key not in local:
            merged.pop(key, None)

    if _ACCOUNT_REGISTRY in local or _ACCOUNT_REGISTRY in remote:
        registry = _merge_registry(
            local.get(_ACCOUNT_REGISTRY),
            remote.get(_ACCOUNT_REGISTRY),
        )
        if registry:
            merged[_ACCOUNT_REGISTRY] = registry

    if _EVENT_MODE_MARKER not in merged and _EVENT_MODE_MARKER in remote:
        merged[_EVENT_MODE_MARKER] = copy.deepcopy(remote[_EVENT_MODE_MARKER])

    if "active_wheels" in local:
        merged["active_wheels"] = _merge_active_wheels(
            local.get("active_wheels"),
            remote.get("active_wheels"),
        )
    else:
        merged.pop("active_wheels", None)

    return merged


def _decode_remote(payload: dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(str(payload.get("content") or ""), validate=False)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"runtime_file_decode_failed:{type(exc).__name__}:{exc}"
        ) from exc


def _read_remote_bytes(
    payload: dict[str, Any],
    *,
    repository: str,
    headers: dict[str, str],
) -> bytes:
    """Read a Contents API payload, falling back to Git Blobs above 1 MiB."""

    decoded = _decode_remote(payload)
    try:
        remote_size = int(payload.get("size", 0) or 0)
    except (TypeError, ValueError):
        remote_size = 0
    remote_encoding = str(payload.get("encoding") or "").strip().casefold()
    if decoded or (remote_size == 0 and remote_encoding != "none"):
        return decoded

    blob_sha = str(payload.get("sha") or "").strip()
    if not blob_sha:
        raise RuntimeError("runtime_file_decode_failed:missing_blob_sha")
    response = requests.get(
        f"https://api.github.com/repos/{repository}/git/blobs/{blob_sha}",
        headers=headers,
        timeout=PUBLISH_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise RuntimeError(
            "runtime_blob_read_http_"
            f"{response.status_code}:{response.text[:300]}"
        )
    blob_payload = response.json()
    if not isinstance(blob_payload, dict):
        raise RuntimeError("runtime_blob_decode_failed:not_an_object")
    blob_bytes = _decode_remote(blob_payload)
    if not blob_bytes:
        raise RuntimeError("runtime_blob_decode_failed:empty_content")
    return blob_bytes


def publish_monitor_runtime(
    local_path: Path,
    *,
    token: str,
    repository: str,
    branch: str = RUNTIME_STATE_BRANCH,
    attempts: int = PUBLISH_ATTEMPTS,
) -> dict[str, Any]:
    if not token or not repository:
        raise RuntimeError("GitHub credentials are required for runtime publish")
    if not local_path.is_file():
        raise RuntimeError(f"runtime file is missing: {local_path}")

    semantic_state = local_path.name == "state.json"
    local_bytes = local_path.read_bytes()
    local_state = _load_json(local_path) if semantic_state else None
    url = f"https://api.github.com/repos/{repository}/contents/{local_path.name}"
    headers = _headers(token)
    last_error = ""

    for attempt in range(1, max(1, attempts) + 1):
        response = requests.get(
            url,
            headers=headers,
            params={"ref": branch},
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            remote_sha = ""
            remote_bytes = b""
            remote_state: dict[str, Any] = {}
        elif response.status_code == 200:
            payload = response.json()
            remote_sha = str(payload.get("sha") or "")
            remote_bytes = _read_remote_bytes(
                payload,
                repository=repository,
                headers=headers,
            )
            if semantic_state:
                try:
                    decoded = json.loads(remote_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        "runtime_state_decode_failed:"
                        f"{type(exc).__name__}:{exc}"
                    ) from exc
                if not isinstance(decoded, dict):
                    raise RuntimeError("runtime_state_decode_failed:not_an_object")
                remote_state = decoded
            else:
                remote_state = {}
        else:
            raise RuntimeError(
                f"runtime_file_read_http_{response.status_code}:{response.text[:300]}"
            )

        if semantic_state:
            assert local_state is not None
            merged_state = merge_monitor_runtime_state(local_state, remote_state)
            publish_bytes = (
                json.dumps(merged_state, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            unchanged = merged_state == remote_state
        else:
            merged_state = None
            publish_bytes = local_bytes
            unchanged = publish_bytes == remote_bytes

        if unchanged:
            if merged_state is not None:
                _write_json(local_path, merged_state)
            return {
                "file": local_path.name,
                "branch": branch,
                "attempt": attempt,
                "changed": False,
                "sha": remote_sha,
            }

        body: dict[str, Any] = {
            "message": f"Publish BB V.G. runtime {local_path.name} [skip ci]",
            "content": base64.b64encode(publish_bytes).decode("ascii"),
            "branch": branch,
        }
        if remote_sha:
            body["sha"] = remote_sha
        update = requests.put(
            url,
            headers=headers,
            json=body,
            timeout=PUBLISH_TIMEOUT_SECONDS,
        )
        if update.status_code in {200, 201}:
            if merged_state is not None:
                _write_json(local_path, merged_state)
            result = update.json()
            commit = result.get("commit") if isinstance(result, dict) else {}
            return {
                "file": local_path.name,
                "branch": branch,
                "attempt": attempt,
                "changed": True,
                "sha": str(commit.get("sha") or ""),
            }

        last_error = f"http_{update.status_code}:{update.text[:300]}"
        if update.status_code not in {409, 422}:
            break
        if attempt < attempts:
            time.sleep(min(attempt, 3))

    raise RuntimeError(
        f"runtime_file_publish_failed_after_{attempts}_attempts:"
        f"{local_path.name}:{last_error}"
    )


def self_test() -> None:
    event = {
        "wheel_key": "wheel",
        "action_id": 42,
        "server_start_at": "2026-07-26T12:00:00+00:00",
    }
    local = {
        "monitor_field": "fresh",
        "active_wheels": {
            "wheel": {
                **event,
                "last_checked_at": "2026-07-26T12:02:00+00:00",
            }
        },
        "auto_participation_events": {
            "evt:test": {
                "status": "queued",
                "recorded_at": "2026-07-26T12:00:10+00:00",
            }
        },
    }
    remote = {
        "stale_monitor_field": "must-not-return",
        "active_wheels": {
            "wheel": {
                **event,
                "participating": True,
                "auto_participation_status": "participated",
            },
            "closed": {
                "wheel_key": "closed",
                "action_id": 1,
                "server_start_at": "2026-07-26T10:00:00+00:00",
                "participating": True,
            },
        },
        "auto_participation_events": {
            "evt:test": {
                "status": "participated",
                "attempted_at": "2026-07-26T12:01:00+00:00",
            }
        },
    }
    merged = merge_monitor_runtime_state(local, remote)
    assert merged["monitor_field"] == "fresh"
    assert "stale_monitor_field" not in merged
    assert set(merged["active_wheels"]) == {"wheel"}
    assert merged["active_wheels"]["wheel"]["last_checked_at"].endswith("12:02:00+00:00")
    assert merged["active_wheels"]["wheel"]["participating"] is True
    assert merged["auto_participation_events"]["evt:test"]["status"] == "participated"
    print("monitor runtime publisher self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish-monitor-runtime", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--runtime-state-branch",
        default=os.getenv("BBVG_RUNTIME_STATE_BRANCH", RUNTIME_STATE_BRANCH),
    )
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.publish_monitor_runtime:
        parser.error("--publish-monitor-runtime is required")

    outbox_reconciliation: dict[str, int] = {}
    if args.publish_monitor_runtime.name in {"state.json", "source_health.json"}:
        state_path = args.publish_monitor_runtime.parent / "state.json"
        if state_path.is_file():
            outbox_reconciliation = reconcile_runtime_outbox(state_path)
            refresh_functional_health(
                args.publish_monitor_runtime.parent / "source_health.json"
            )

    result = publish_monitor_runtime(
        args.publish_monitor_runtime,
        token=os.getenv("GH_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip(),
        repository=os.getenv("GITHUB_REPOSITORY", "").strip(),
        branch=str(args.runtime_state_branch or RUNTIME_STATE_BRANCH),
    )
    if outbox_reconciliation:
        result["outbox_reconciliation"] = outbox_reconciliation
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
