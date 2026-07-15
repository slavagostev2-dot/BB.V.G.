from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def current_commit(root: Path = ROOT) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()


def verify_current_commit(expected: str, root: Path = ROOT) -> str:
    required = str(expected or "").strip()
    if not required:
        raise ValueError("Expected commit SHA is required")
    actual = current_commit(root)
    if actual != required:
        raise RuntimeError(
            f"CI checked out {actual}, but the workflow event requires {required}"
        )
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", default=os.getenv("GITHUB_SHA", ""))
    args = parser.parse_args()
    verified = verify_current_commit(args.expected)
    print(f"Verified current workflow commit: {verified}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
