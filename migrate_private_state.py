from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib import request

ROOT = Path(__file__).resolve().parent


def _decode(raw: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _current(path: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return _decode((ROOT / path).read_text(encoding="utf-8"), default)
    except OSError:
        return dict(default)


def latest_matching(
    path: str,
    predicate: Callable[[dict[str, Any]], bool],
    default: dict[str, Any],
) -> dict[str, Any]:
    value = _current(path, default)
    if predicate(value):
        return value
    try:
        commits = subprocess.check_output(
            ["git", "log", "--format=%H", "--", path],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).splitlines()
    except (OSError, subprocess.CalledProcessError):
        commits = []
    for commit in commits:
        try:
            raw = subprocess.check_output(
                ["git", "show", f"{commit}:{path}"],
                cwd=ROOT,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        value = _decode(raw, default)
        if predicate(value):
            return value
    return dict(default)


def deployment_url() -> str:
    value = _current("private_state_deployment.json", {})
    if value.get("status") != "deployed":
        return ""
    url = str(value.get("url") or "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


def put_json(base_url: str, token: str, path: str, value: dict[str, Any]) -> None:
    req = request.Request(
        base_url + path,
        data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(str(payload.get("error") or "Migration rejected"))


def migrate() -> tuple[int, int]:
    base_url = str(os.getenv("BBVG_STATE_API_URL") or deployment_url()).rstrip("/")
    token = str(os.getenv("BBVG_STATE_API_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not base_url or not token:
        raise RuntimeError("Private state API URL and token are required")

    access = latest_matching(
        "bot_access.json",
        lambda value: bool(value.get("users")),
        {},
    )
    source_requests = latest_matching(
        "source_requests.json",
        lambda value: bool(value.get("requests")),
        {"version": 1, "requests": {}},
    )
    user_count = len(access.get("users") or {})
    request_count = len(source_requests.get("requests") or {})
    if user_count:
        put_json(base_url, token, "/v1/admin/access", access)
    if request_count:
        put_json(base_url, token, "/v1/admin/source-requests", source_requests)
    return user_count, request_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    users = latest_matching("bot_access.json", lambda value: bool(value.get("users")), {})
    requests_state = latest_matching(
        "source_requests.json",
        lambda value: bool(value.get("requests")),
        {"version": 1, "requests": {}},
    )
    if args.check:
        print(
            "Migration source check passed: "
            f"users={len(users.get('users') or {})}, "
            f"source_requests={len(requests_state.get('requests') or {})}"
        )
        return 0
    user_count, request_count = migrate()
    print(f"Migration completed: users={user_count}, source_requests={request_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
