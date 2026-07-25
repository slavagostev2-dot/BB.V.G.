from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bbvg.storage.event_store import SCHEMA_VERSION


UTC = timezone.utc
COMPATIBILITY_EPOCH = "durable-event-ledger-v1"


def _sha(status: dict[str, Any]) -> str:
    value = str(status.get("head_sha") or status.get("code_sha") or "").strip()
    return value.casefold() if len(value) == 40 else ""


def build_manifest(
    monitor_status: dict[str, Any],
    control_center_status: dict[str, Any],
) -> dict[str, Any]:
    """Build the one runtime compatibility view used by health and audits."""

    monitor_sha = _sha(monitor_status)
    control_sha = _sha(control_center_status)
    components = {
        "monitor": {
            "sha": monitor_sha,
            "started_at": str(
                monitor_status.get("run_started_at")
                or monitor_status.get("started_at")
                or ""
            ),
            "run_id": str(
                monitor_status.get("workflow_run_id")
                or monitor_status.get("run_id")
                or ""
            ),
        },
        "control_center": {
            "sha": control_sha,
            "started_at": str(control_center_status.get("started_at") or ""),
            "run_id": str(
                control_center_status.get("workflow_run_id")
                or control_center_status.get("run_id")
                or ""
            ),
        },
        # Dispatch is pinned to the monitor's exact GITHUB_SHA. Browser runs in
        # the dispatched workflow checkout, so these are one deployable unit.
        "dispatcher": {"sha": monitor_sha},
        "browser_worker": {"sha": monitor_sha},
    }
    shas = {str(item["sha"]) for item in components.values() if item.get("sha")}
    complete = all(item.get("sha") for item in components.values())
    compatible = complete and len(shas) == 1
    return {
        "manifest_schema_version": 1,
        "compatibility_epoch": COMPATIBILITY_EPOCH,
        "event_ledger_schema_version": SCHEMA_VERSION,
        "legacy_state_schema_version": "compatibility-json-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "components": components,
        "compatible": compatible,
        "compatibility_error": (
            ""
            if compatible
            else "component SHA mismatch or missing runtime status"
        ),
    }


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor", type=Path, default=Path("monitor_status.json"))
    parser.add_argument(
        "--control",
        type=Path,
        default=Path("admin_panel_status.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args(argv)
    manifest = build_manifest(_read(args.monitor), _read(args.control))
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 1 if args.require_compatible and not manifest["compatible"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
