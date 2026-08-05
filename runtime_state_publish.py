from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

import requests

from auto_participation_bot_sync import merge_auto_participation_state

GITHUB_API_VERSION = "2022-11-28"
RUNTIME_STATE_BRANCH = "runtime-state"
PUBLISH_ATTEMPTS = 5
REQUEST_TIMEOUT_SECONDS = 20


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"invalid_local_runtime_state:{type(exc).__name__}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("invalid_local_runtime_state:not_an_object")
    return value


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "BB-VG-runtime-state-publisher",
    }


def _json_object(response: Any, label: str) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{label}_invalid_json:{type(exc).__name__}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}_not_an_object")
    return value


def _read_remote_state(
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    request_get: Callable[..., Any] = requests.get,
) -> tuple[dict[str, Any], str]:
    encoded = str(payload.get("content") or "").strip()
    if encoded:
        try:
            raw = base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                f"runtime_state_base64_decode_failed:{type(exc).__name__}:{exc}"
            ) from exc
        read_mode = "contents_base64"
    else:
        download_url = str(payload.get("download_url") or "").strip()
        if not download_url:
            raise RuntimeError("runtime_state_content_and_download_url_missing")
        response = request_get(
            download_url,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"runtime_state_download_http_{response.status_code}:"
                f"{str(getattr(response, 'text', ''))[:300]}"
            )
        raw = bytes(getattr(response, "content", b"") or b"")
        if not raw:
            raise RuntimeError("runtime_state_download_empty")
        read_mode = "download_url"

    try:
        remote = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"runtime_state_decode_failed:{type(exc).__name__}:{exc}"
        ) from exc
    if not isinstance(remote, dict):
        raise RuntimeError("runtime_state_not_an_object")
    return remote, read_mode


def publish_runtime_state(
    local_path: Path,
    *,
    token: str,
    repository: str,
    branch: str = RUNTIME_STATE_BRANCH,
    attempts: int = PUBLISH_ATTEMPTS,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    request_get: Callable[..., Any] = requests.get,
    request_put: Callable[..., Any] = requests.put,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """CAS-merge worker outcomes into runtime-state, including files over 1 MB."""

    local = _load_json(local_path)
    if not token or not repository:
        raise RuntimeError("GitHub credentials are required for runtime-state publish")

    url = f"https://api.github.com/repos/{repository}/contents/state.json"
    headers = _headers(token)
    last_error = "unknown"

    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = request_get(
                url,
                headers=headers,
                params={"ref": branch},
                timeout=timeout,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"runtime_state_read_http_{response.status_code}:"
                    f"{str(getattr(response, 'text', ''))[:300]}"
                )
            payload = _json_object(response, "runtime_state_contents")
            sha = str(payload.get("sha") or "").strip()
            if not sha:
                raise RuntimeError("runtime_state_sha_missing")
            remote, read_mode = _read_remote_state(
                payload,
                headers=headers,
                timeout=timeout,
                request_get=request_get,
            )
        except (RuntimeError, requests.RequestException) as exc:
            last_error = f"{type(exc).__name__}:{exc}"
            if attempt < max(1, attempts):
                sleep(min(float(attempt), 3.0))
                continue
            break

        merged = merge_auto_participation_state(remote, local)
        if merged == remote:
            return {
                "branch": branch,
                "attempt": attempt,
                "changed": False,
                "sha": sha,
                "read_mode": read_mode,
            }

        encoded = base64.b64encode(
            (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii")
        try:
            update = request_put(
                url,
                headers=headers,
                json={
                    "message": "Publish auto participation outcome [skip ci]",
                    "content": encoded,
                    "branch": branch,
                    "sha": sha,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}:{exc}"
            if attempt < max(1, attempts):
                sleep(min(float(attempt), 3.0))
                continue
            break

        if update.status_code in {200, 201}:
            result = _json_object(update, "runtime_state_update")
            commit = (
                result.get("commit")
                if isinstance(result.get("commit"), dict)
                else {}
            )
            return {
                "branch": branch,
                "attempt": attempt,
                "changed": True,
                "sha": str(commit.get("sha") or ""),
                "read_mode": read_mode,
            }

        last_error = (
            f"http_{update.status_code}:"
            f"{str(getattr(update, 'text', ''))[:300]}"
        )
        if update.status_code not in {409, 422, 429, 500, 502, 503, 504}:
            break
        if attempt < max(1, attempts):
            sleep(min(float(attempt), 3.0))

    raise RuntimeError(
        f"runtime_state_publish_failed_after_{attempts}_attempts:{last_error}"
    )


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        payload: dict[str, Any] | None = None,
        content: bytes = b"",
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = text

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


def self_test() -> None:
    large_remote = {
        "version": 6,
        "padding": "x" * 1_100_000,
    }
    raw = (json.dumps(large_remote, ensure_ascii=False) + "\n").encode("utf-8")
    calls: list[str] = []

    def fake_get(url: str, **_kwargs: Any) -> _FakeResponse:
        calls.append(url)
        if "/contents/state.json" in url:
            return _FakeResponse(
                200,
                payload={
                    "sha": "state-sha",
                    "content": "",
                    "download_url": "https://example.invalid/state.json",
                },
            )
        return _FakeResponse(200, content=raw)

    with TemporaryDirectory() as folder:
        path = Path(folder) / "state.json"
        path.write_text("{}\n", encoding="utf-8")
        result = publish_runtime_state(
            path,
            token="test-token",
            repository="owner/repo",
            attempts=1,
            request_get=fake_get,
            request_put=lambda *_args, **_kwargs: _FakeResponse(500),
            sleep=lambda _seconds: None,
        )
    assert result["changed"] is False
    assert result["read_mode"] == "download_url"
    assert len(raw) > 1_000_000
    assert calls == [
        "https://api.github.com/repos/owner/repo/contents/state.json",
        "https://example.invalid/state.json",
    ]

    encoded = base64.b64encode(b'{"version": 1}').decode("ascii")
    direct, mode = _read_remote_state(
        {"content": encoded},
        headers={},
        request_get=fake_get,
    )
    assert direct == {"version": 1}
    assert mode == "contents_base64"
    print("runtime-state large-file publisher self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state_path", type=Path, nargs="?")
    parser.add_argument(
        "--publish-runtime-state",
        dest="publish_runtime_state_path",
        type=Path,
    )
    parser.add_argument(
        "--branch",
        default=os.getenv("BBVG_RUNTIME_STATE_BRANCH", RUNTIME_STATE_BRANCH),
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    state_path = args.publish_runtime_state_path or args.state_path
    if state_path is None:
        parser.error(
            "state_path or --publish-runtime-state state.json is required"
        )
    result = publish_runtime_state(
        state_path,
        token=os.getenv("GH_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip(),
        repository=os.getenv("GITHUB_REPOSITORY", "").strip(),
        branch=str(args.branch or RUNTIME_STATE_BRANCH),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
