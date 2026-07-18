from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import monitor_data as data_store

ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "source_registry.json"


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def read_sources(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in lines:
        value = raw.split("#", 1)[0].strip().lstrip("@")
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def mapping_entry(mapping: object, username: str) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    target = username.casefold()
    for key, value in mapping.items():
        if str(key).casefold() == target and isinstance(value, dict):
            return value
    return {}


def status_reason(entry: dict[str, Any], checked: bool, available: bool) -> str:
    if available:
        return "источник доступен"
    for key in ("failure_reason", "last_error", "detail", "reason"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value[:240]
    raw_status = str(entry.get("status") or "").strip()
    if checked and raw_status:
        return raw_status
    return "ожидает первой проверки"


def build_registry(root: Path = ROOT, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    primary = read_sources(root / "public_sources.txt")
    nightly = read_sources(root / "source_catalog.txt")

    configured: dict[str, dict[str, str]] = {}
    for tier, values in (("primary", primary), ("nightly", nightly)):
        for username in values:
            configured.setdefault(username.casefold(), {"username": username, "tier": tier})

    health = load_json(root / "source_health.json", {"sources": {}})
    discovery = load_json(root / "discovery_state.json", {})
    stats = load_json(root / "source_stats.json", {"sources": {}})
    health_sources = health.get("sources") if isinstance(health.get("sources"), dict) else {}
    discovery_sources = discovery.get("health_sources")
    if not isinstance(discovery_sources, dict):
        discovery_sources = discovery.get("sources") if isinstance(discovery.get("sources"), dict) else {}
    stats_sources = stats.get("sources") if isinstance(stats.get("sources"), dict) else {}

    rows: list[dict[str, Any]] = []
    for item in configured.values():
        username = item["username"]
        direct_health = mapping_entry(health_sources, username)
        discovery_health = mapping_entry(discovery_sources, username)
        source_health = direct_health or discovery_health
        source_stats = mapping_entry(stats_sources, username)
        checks = int(source_health.get("checks", source_stats.get("checks", 0)) or 0)
        last_checked_at = str(
            source_health.get("last_checked_at")
            or source_health.get("checked_at")
            or source_stats.get("last_checked_at")
            or ""
        )
        checked = bool(checks or last_checked_at)
        raw_status = str(source_health.get("status") or "unknown").casefold()
        available = raw_status == "ok"
        if available:
            public_status = "available"
        elif checked:
            public_status = "unavailable"
        else:
            public_status = "pending"
        rows.append(
            {
                "username": username,
                "tier": item["tier"],
                "status": public_status,
                "raw_status": raw_status,
                "checked": checked,
                "available": available,
                "reason": status_reason(source_health, checked, available),
                "checks": checks,
                "last_checked_at": last_checked_at or None,
                "last_success_at": source_health.get("last_success_at"),
                "wheel_posts": int(source_stats.get("wheel_posts", 0) or 0),
                "quality_score": int(source_stats.get("quality_score", 0) or 0),
                "admin_confirmed_wheels": int(source_stats.get("admin_confirmed_wheels", 0) or 0),
                "admin_rejected_wheels": int(source_stats.get("admin_rejected_wheels", 0) or 0),
            }
        )

    rows.sort(key=lambda row: (row["tier"] != "primary", str(row["username"]).casefold()))
    summary = {
        "total": len(rows),
        "primary": sum(row["tier"] == "primary" for row in rows),
        "nightly": sum(row["tier"] == "nightly" for row in rows),
        "checked": sum(bool(row["checked"]) for row in rows),
        "available": sum(bool(row["available"]) for row in rows),
        "unavailable": sum(row["status"] == "unavailable" for row in rows),
        "pending": sum(row["status"] == "pending" for row in rows),
    }
    return {
        "version": 2,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "summary": summary,
        "sources": rows,
    }


def write_registry(root: Path = ROOT) -> dict[str, Any]:
    value = build_registry(root)
    path = root / "source_registry.json"
    existing = load_json(path, {})
    if (
        existing.get("version") == value["version"]
        and existing.get("summary") == value["summary"]
        and existing.get("sources") == value["sources"]
    ):
        previous_generated_at = str(existing.get("generated_at") or "").strip()
        if previous_generated_at:
            value["generated_at"] = previous_generated_at
    data_store.atomic_write_json(path, value)
    return value


def self_test() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "public_sources.txt").write_text("Alpha\nBeta\n", encoding="utf-8")
        (root / "source_catalog.txt").write_text("Gamma\n", encoding="utf-8")
        (root / "source_health.json").write_text(
            json.dumps(
                {
                    "sources": {
                        "Alpha": {"status": "ok", "checks": 2, "last_checked_at": "2026-07-14T00:00:00+00:00"},
                        "Beta": {"status": "error", "checks": 1, "failure_reason": "канал недоступен"},
                    }
                }
            ),
            encoding="utf-8",
        )
        (root / "discovery_state.json").write_text("{}", encoding="utf-8")
        (root / "source_stats.json").write_text(
            json.dumps({"sources": {"Alpha": {"quality_score": 40}}}),
            encoding="utf-8",
        )
        value = build_registry(root, datetime(2026, 7, 14, tzinfo=timezone.utc))
        assert value["summary"] == {
            "total": 3,
            "primary": 2,
            "nightly": 1,
            "checked": 2,
            "available": 1,
            "unavailable": 1,
            "pending": 1,
        }
        assert value["sources"][0]["quality_score"] == 40
        value["generated_at"] = "2026-07-14T00:00:00+00:00"
        (root / "source_registry.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
        stable = write_registry(root)
        assert stable["generated_at"] == "2026-07-14T00:00:00+00:00"
    print("source_registry unified inventory self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    value = write_registry()
    summary = value["summary"]
    print(
        "BB V.G. source registry: "
        f"{summary['total']} total, {summary['checked']} checked, "
        f"{summary['available']} available, {summary['unavailable']} unavailable, "
        f"{summary['pending']} pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
