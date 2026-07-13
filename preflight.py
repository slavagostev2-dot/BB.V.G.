from __future__ import annotations

import json
from pathlib import Path

import monitor_data as data_store

ROOT = Path(__file__).resolve().parent


def require_text(path: str, markers: tuple[str, ...]) -> None:
    file_path = ROOT / path
    if not file_path.is_file():
        raise SystemExit(f"PRECHECK ERROR: missing file: {path}")
    text = file_path.read_text(encoding="utf-8")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(
            f"PRECHECK ERROR: {path} has wrong contents; missing markers: {missing}"
        )


def read_json(path: str) -> dict:
    try:
        value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"PRECHECK ERROR: invalid {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"PRECHECK ERROR: {path} must contain a JSON object")
    return value


def main() -> None:
    require_text("requirements.txt", ("requests==", "beautifulsoup4==", "tzdata"))
    require_text(
        "monitor.py",
        (
            "from __future__ import annotations",
            "def main()",
            "process_bot_feedback",
            "def process_active_wheels",
            "def active_wheels_text",
            "def maybe_send_source_inactivity_report",
            "✅ Участвую",
        ),
    )
    require_text(
        "monitor_data.py",
        (
            "def load_health",
            "def load_stats",
            "def operational_sources",
            "def sources_without_recent_wheels",
        ),
    )
    require_text(
        "bbvg_monitor_runtime.py",
        (
            "MANUAL_WHEEL_TTL_DAYS",
            "inactive_wheels",
            "needs_manual_time",
            "remember_without_pending",
            "🚫 Неактивное",
        ),
    )
    require_text(
        "bbvg_monitor_main.py",
        (
            "recover_deadline_manual_first",
            "process_active_without_unknown_time_spam",
        ),
    )
    require_text(
        "admin_panel_runtime_v20.py",
        (
            "BB V.G.",
            "Указать время",
            "Неактивное",
            "hide_wheel_for_current_user",
        ),
    )
    require_text("nightly_discovery.py", ("import monitor", "def main()"))
    require_text("daily_report.py", ("Ежедневный отчёт", "BB V.G.", "def main()"))
    require_text("telegram_monitor.py", ("from monitor import main", "raise SystemExit(main())"))
    require_text("self_test.py", ("import monitor", "def main()"))
    require_text("public_sources.txt", ("narodCast", "kolesaBB"))
    require_text("source_catalog.txt", ("Ночное наблюдение",))
    require_text(".github/workflows/daily-report.yml", ("BB V.G. daily report", "daily_report.py"))

    mapping = read_json("identifier_sources.json")
    if not isinstance(mapping.get("mappings"), list):
        raise SystemExit("PRECHECK ERROR: identifier_sources.json has an unexpected structure")

    catalog = read_json("partners_catalog.json")
    if not isinstance(catalog.get("entities"), list):
        raise SystemExit("PRECHECK ERROR: partners_catalog.json has an unexpected structure")

    for path, key in (
        ("source_health.json", "sources"),
        ("source_stats.json", "sources"),
        ("unknown_timer_samples.json", "samples"),
    ):
        value = read_json(path)
        if key not in value:
            raise SystemExit(f"PRECHECK ERROR: {path} is missing key {key}")

    fast_values = [
        data_store.clean_username(line.split("#", 1)[0])
        for line in (ROOT / "public_sources.txt").read_text(encoding="utf-8").splitlines()
        if data_store.clean_username(line.split("#", 1)[0])
    ]
    nightly_values = [
        data_store.clean_username(line.split("#", 1)[0])
        for line in (ROOT / "source_catalog.txt").read_text(encoding="utf-8").splitlines()
        if data_store.clean_username(line.split("#", 1)[0])
    ]
    fast = {
        item.casefold()
        for item in data_store.operational_sources(fast_values, "fast")
    }
    nightly = {
        item.casefold()
        for item in data_store.operational_sources(nightly_values, "nightly")
    }
    overlap = sorted(fast & nightly)
    if overlap:
        raise SystemExit(
            "PRECHECK ERROR: fast and nightly source lists overlap: " + ", ".join(overlap)
        )

    forbidden = {"frixa_betboom", "gazazor"}
    stale = sorted((fast | nightly) & forbidden)
    if stale:
        raise SystemExit("PRECHECK ERROR: removed sources are still operational: " + ", ".join(stale))
    if "narodcast" not in fast:
        raise SystemExit("PRECHECK ERROR: narodCast must remain in the fast list")

    metadata = data_store.flatten_partner_channels(catalog)
    narod = metadata.get("narodcast", {})
    if narod.get("relationship") != "betboom_partner":
        raise SystemExit("PRECHECK ERROR: narodCast must be classified as a partner source")
    if any(info.get("relationship") == "confirmed_ambassador" for info in metadata.values()):
        raise SystemExit("PRECHECK ERROR: confirmed_ambassador classification is obsolete")

    print("BB V.G. preflight checks passed.")


if __name__ == "__main__":
    main()
