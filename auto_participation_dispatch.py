from __future__ import annotations

import os
import time

import requests


def main() -> int:
    """Dispatch the isolated auto-participation workflow after monitor state is persisted."""

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
    if not token or not repository:
        print("Auto participation dispatch skipped: GitHub runtime credentials are missing")
        return 0

    try:
        delay_seconds = max(
            0,
            min(180, int(os.getenv("BETBOOM_DISPATCH_DELAY_SECONDS", "75"))),
        )
    except ValueError:
        delay_seconds = 75
    if delay_seconds:
        time.sleep(delay_seconds)

    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        "auto-participation.yml/dispatches"
    )
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": branch},
        timeout=15,
    )
    if response.status_code == 204:
        print(
            "Auto participation workflow dispatched: "
            f"repository={repository} ref={branch} workflow=auto-participation.yml"
        )
        return 0

    print(
        "Auto participation dispatch failed: "
        f"repository={repository} ref={branch} workflow=auto-participation.yml "
        f"HTTP {response.status_code} {response.text[:500]}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
