from __future__ import annotations

import json
from pathlib import Path

import monitor_data as data_store

ROOT = Path(__file__).resolve().parent
EXPECTED_SOURCE_COUNT = 66


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


def source_values(path: str) -> list[str]:
    return [
        data_store.clean_username(line.split("#", 1)[0])
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if data_store.clean_username(line.split("#", 1)[0])
    ]


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
            "telegram_transport",
        ),
    )
    require_text(
        "telegram_transport.py",
        (
            'PRIMARY_DOMAIN = os.getenv("TELEGRAM_WEB_DOMAIN", "telegram.me")',
            "def public_source_url",
            "def rewrite_telegram_url",
            "def resolve_primary_ipv4",
        ),
    )
    require_text(
        "incident_manager.py",
        ("def reconcile", "open_notification_pending", "resolution_notification_pending"),
    )
    require_text(
        "system_checks.py",
        ("EXPECTED_SOURCE_COUNT", "check_telegram_web", "check_notification_routing"),
    )
    require_text(
        "monitor_health.py",
        (
            "def record_iteration",
            "def health_check",
            "consecutive_no_progress",
            "last_successful_iteration_at",
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
    require_text(
        "admin_panel_runtime_v21.py",
        (
            "def notify_owner_about_new_user",
            "def handle_update",
            "Новый пользователь BB V.G.",
            "self.load_access(force=True)",
            "Открыть список пользователей",
            "my_chat_member",
        ),
    )
    require_text("nightly_discovery.py", ("import monitor", "def main()"))
    require_text("nightly_discovery_entry.py", ("telegram_transport.install", "fetch_page_on_primary_domain"))
    require_text("source_intelligence_entry.py", ("telegram_transport.install", "source_intelligence.main"))
    require_text("daily_report.py", ("Ежедневный отчёт", "BB V.G.", "def main()"))
    require_text("telegram_monitor.py", ("from monitor import main", "raise SystemExit(main())"))
    require_text("self_test.py", ("import monitor", "def main()"))
    require_text("public_sources.txt", ("narodCast", "kolesaBB", "betboomteamcs2"))
    require_text("source_catalog.txt", ("Ночной мониторинг", "7 дней"))
    require_text(".github/workflows/daily-report.yml", ("BB V.G. daily report", "daily_report.py"))

    active_domain_files = (
        "monitor.py",
        "nightly_discovery.py",
        "admin_panel_runtime_v17.py",
        "admin_panel_runtime_v21.py",
        "docs/app.js",
        "docs/bbvg-controls.js",
        "docs/views-secondary.js",
    )
    legacy_domain_files = [
        path
        for path in active_domain_files
        if "https://t.me/" in (ROOT / path).read_text(encoding="utf-8")
    ]
    if legacy_domain_files:
        raise SystemExit(
            "PRECHECK ERROR: blocked Telegram domain remains in runtime: "
            + ", ".join(legacy_domain_files)
        )
    require_text(
        "docs/app.js",
        (
            "lightTheme",
            "participationHistory",
            "adminRatingsActive",
            "HapticFeedback",
            "setHeaderColor",
            "setBackgroundColor",
            "setBottomBarColor",
            "openNotificationSettings",
        ),
    )
    require_text(
        "docs/bbvg-controls.js",
        (
            "data-action=\"notifications\"",
            "app.days===1?'':",
            "Активные колёса",
            "Всего участий",
            'class="card profile-summary"',
            'class="source-count-note"',
        ),
    )
    require_text(
        "docs/index.html",
        (
            'rel="preload" as="image" href="splash-3d.webp',
            '<div id="splash" class="splash" aria-label="Загрузка BB V.G."><img',
            'id="headerAvatar"',
            'id="headerUserName"',
        ),
    )
    splash_art = ROOT / "docs" / "splash-3d.webp"
    if not splash_art.is_file() or splash_art.stat().st_size < 10_000:
        raise SystemExit("PRECHECK ERROR: unified Mini App splash artwork is missing or empty")
    require_text("docs/styles.css", ("--chart-columns", ".theme-moon", ".profile-settings"))
    controls_source = (ROOT / "docs/bbvg-controls.js").read_text(encoding="utf-8")
    if 'data-setting="lightTheme"' in controls_source:
        raise SystemExit("PRECHECK ERROR: duplicate profile theme switch returned")
    if 'class="card profile-head"' in controls_source:
        raise SystemExit("PRECHECK ERROR: duplicate profile identity returned")
    source_panel = controls_source.split("renderSources=function(){", 1)[-1].split(
        "renderProfile=function(){", 1
    )[0]
    if 'class="stats-grid"' in source_panel:
        raise SystemExit("PRECHECK ERROR: oversized source overview returned")
    if "serviceWorker.register" in (ROOT / "docs/app.js").read_text(encoding="utf-8"):
        raise SystemExit("PRECHECK ERROR: stale Mini App service worker registration returned")

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

    stats = read_json("source_stats.json")
    if stats.get("source_rating_epoch_day") != "2026-07-14":
        raise SystemExit("PRECHECK ERROR: source rating epoch was not reset")

    configured_values = source_values("public_sources.txt")
    nightly_values = source_values("source_catalog.txt")
    all_values = configured_values + nightly_values
    configured_keys = [item.casefold() for item in all_values]
    if len(set(configured_keys)) < EXPECTED_SOURCE_COUNT:
        raise SystemExit(
            f"PRECHECK ERROR: expected at least {EXPECTED_SOURCE_COUNT} approved sources across both tiers, found {len(set(configured_keys))}"
        )
    if len(configured_keys) != len(set(configured_keys)):
        raise SystemExit("PRECHECK ERROR: source tiers contain duplicate or overlapping sources")

    fast = {
        item.casefold()
        for item in data_store.operational_sources(configured_values, "fast")
    }
    nightly = {
        item.casefold()
        for item in data_store.operational_sources(nightly_values, "nightly")
    }
    approved = fast | nightly
    if len(approved) < EXPECTED_SOURCE_COUNT:
        raise SystemExit(
            f"PRECHECK ERROR: operational source union must contain at least {EXPECTED_SOURCE_COUNT}; found {len(approved)}"
        )
    if fast & nightly:
        raise SystemExit("PRECHECK ERROR: primary and nightly source tiers overlap")

    forbidden = {"frixa_betboom", "gazazor"}
    stale = sorted(approved & forbidden)
    if stale:
        raise SystemExit("PRECHECK ERROR: removed sources are still operational: " + ", ".join(stale))
    if "narodcast" not in approved:
        raise SystemExit("PRECHECK ERROR: narodCast must remain in the approved source pool")

    metadata = data_store.flatten_partner_channels(catalog)
    narod = metadata.get("narodcast", {})
    if narod.get("relationship") != "betboom_partner":
        raise SystemExit("PRECHECK ERROR: narodCast must be classified as a partner source")
    if any(info.get("relationship") == "confirmed_ambassador" for info in metadata.values()):
        raise SystemExit("PRECHECK ERROR: confirmed_ambassador classification is obsolete")

    print(
        f"BB V.G. preflight checks passed: {len(approved)} approved telegram.me sources "
        f"({len(fast)} primary, {len(nightly)} nightly)."
    )


if __name__ == "__main__":
    main()
