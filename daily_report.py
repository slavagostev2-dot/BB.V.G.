from __future__ import annotations

import html
import json
import os
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

import monitor
import monitor_data as data_store


BRAND_NAME = "BB V.G."
PERIODS = {
    "daily": (1, "Ежедневная"),
    "weekly": (7, "Еженедельная"),
    "monthly": (30, "Ежемесячная"),
}


def counter(value: dict, name: str) -> int:
    return int(value.get(name, 0)) if isinstance(value, dict) else 0


def load_discovery() -> dict:
    try:
        value = json.loads(
            (monitor.ROOT / "discovery_state.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def merge_numeric_dict(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            target[key] = int(target.get(key, 0)) + int(value)


def combined_stats(fast_stats: dict, discovery: dict) -> dict:
    result = deepcopy(fast_stats)
    result.setdefault("sources", {})
    result.setdefault("daily", {})

    for source, entry in discovery.get("stats_sources", {}).items():
        if not isinstance(entry, dict):
            continue
        target = result["sources"].setdefault(source, {})
        merge_numeric_dict(target, entry)
        for key in ("last_updated_at", "last_wheel_post_at", "last_activation_at"):
            if entry.get(key) and str(entry[key]) > str(target.get(key, "")):
                target[key] = entry[key]

    for day, entry in discovery.get("stats_daily", {}).items():
        if not isinstance(entry, dict):
            continue
        day_target = result["daily"].setdefault(day, {"sources": {}, "totals": {}})
        merge_numeric_dict(day_target.setdefault("totals", {}), entry.get("totals", {}))
        for source, source_entry in entry.get("sources", {}).items():
            if not isinstance(source_entry, dict):
                continue
            source_target = day_target.setdefault("sources", {}).setdefault(source, {})
            merge_numeric_dict(source_target, source_entry)
    return result


def combined_health(fast_health: dict, discovery: dict) -> dict:
    result = {"version": 1, "sources": {}}
    for username, entry in discovery.get("health_sources", {}).items():
        if isinstance(entry, dict):
            result["sources"][username] = deepcopy(entry)
    for username, entry in fast_health.get("sources", {}).items():
        if isinstance(entry, dict):
            result["sources"][username] = deepcopy(entry)
    return result


def truthy_env(name: str) -> bool:
    return str(os.getenv(name, "")).strip().casefold() in {"1", "true", "yes", "on"}


def report_dates(period: str, *, current: date, manual: bool) -> tuple[list[str], date, date]:
    days, _ = PERIODS.get(period, PERIODS["daily"])
    end = current if manual else current - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    values = [(start + timedelta(days=offset)).isoformat() for offset in range(days)]
    return values, start, end


def aggregate_period(stats: dict, allowed_dates: list[str]) -> dict[str, Any]:
    allowed = set(allowed_dates)
    totals: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    day_counts: dict[str, int] = {}

    for day, entry in stats.get("daily", {}).items():
        if day not in allowed or not isinstance(entry, dict):
            continue
        day_totals = entry.get("totals") if isinstance(entry.get("totals"), dict) else {}
        for key, value in day_totals.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + int(value)
        day_counts[day] = int(day_totals.get("wheel_posts", 0) or 0)
        source_rows = entry.get("sources") if isinstance(entry.get("sources"), dict) else {}
        for source, source_entry in source_rows.items():
            if not isinstance(source_entry, dict):
                continue
            count = int(source_entry.get("wheel_posts", 0) or 0)
            if count > 0:
                source_counts[str(source)] = source_counts.get(str(source), 0) + count

    top_sources = sorted(
        source_counts.items(),
        key=lambda item: (-item[1], item[0].casefold()),
    )
    best_day = max(day_counts.items(), key=lambda item: item[1], default=("", 0))
    return {
        "totals": totals,
        "source_counts": source_counts,
        "top_sources": top_sources,
        "best_day": best_day,
    }


def report_text(
    *,
    period: str,
    start: date,
    end: date,
    summary: dict[str, Any],
    state: dict,
    health: dict,
) -> str:
    days, label = PERIODS[period]
    totals = summary["totals"]
    wheel_posts = int(totals.get("wheel_posts", 0) or 0)
    notifications = int(totals.get("preliminary_sent", 0) or 0) + int(
        totals.get("activation_sent", 0) or 0
    )
    top_sources = summary["top_sources"]

    active_rows = [
        (str(key), entry)
        for key, entry in state.get("active_wheels", {}).items()
        if isinstance(entry, dict)
    ]
    active_keys = {
        str(entry.get("identifier") or key).casefold()
        for key, entry in active_rows
    } | {key.casefold() for key, _ in active_rows}
    participating = {
        str(key).casefold()
        for key, entry in state.get("participating_wheels", {}).items()
        if isinstance(entry, dict)
    }
    with_time = sum(
        1 for _, entry in active_rows if monitor.parse_datetime(entry.get("deadline")) is not None
    )

    lines = [f"📊 <b>{label} сводка {BRAND_NAME}</b>"]
    if start == end:
        lines.append(f"Период: <b>{start:%d.%m.%Y}</b>")
    else:
        lines.append(f"Период: <b>{start:%d.%m.%Y}–{end:%d.%m.%Y}</b>")
    lines.append("")

    if wheel_posts > 0:
        lines.append(f"🎡 Публикаций с колёсами: <b>{wheel_posts}</b>")
        lines.append(f"📡 Источников с находками: <b>{len(summary['source_counts'])}</b>")
        if notifications > 0:
            lines.append(f"🔔 Отправлено уведомлений: <b>{notifications}</b>")
        if days > 1:
            lines.append(f"📈 Среднее за день: <b>{wheel_posts / days:.1f}</b>")
            best_day, best_count = summary["best_day"]
            if best_day and best_count > 0:
                lines.append(
                    f"⭐ Самый активный день: <b>{datetime.fromisoformat(best_day):%d.%m.%Y}</b> — {best_count}"
                )
        if top_sources:
            lines.extend(["", "<b>Лучшие источники периода</b>"])
            for index, (source, count) in enumerate(top_sources[:5], 1):
                lines.append(f"{index}. @{html.escape(source)} — {count}")
    else:
        lines.append("За выбранный период новые публикации с колёсами не обнаружены.")

    lines.extend(["", "<b>Сейчас</b>"])
    lines.append(f"🔥 Активных колёс: <b>{len(active_rows)}</b>")
    if active_rows:
        lines.append(f"⏱ Время определено: <b>{with_time} из {len(active_rows)}</b>")
        lines.append(
            f"✅ Участие отмечено: <b>{len(active_keys & participating)} из {len(active_rows)}</b>"
        )

    health_rows = [
        entry for entry in health.get("sources", {}).values() if isinstance(entry, dict)
    ]
    quarantined = sum(1 for entry in health_rows if entry.get("status") == "quarantined")
    problems = sum(
        1 for entry in health_rows if entry.get("status") not in {"ok", "quarantined"}
    )
    if quarantined or problems:
        lines.extend(["", "<b>Требуют внимания</b>"])
        if problems:
            lines.append(f"• Источников с временными проблемами: {problems}")
        if quarantined:
            lines.append(f"• Источников в карантине: {quarantined}")

    return "\n".join(lines)[:4000]


def main() -> int:
    try:
        monitor.validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    period = str(os.getenv("REPORT_PERIOD", "daily")).strip().casefold()
    if period not in PERIODS:
        print(f"Unknown REPORT_PERIOD: {period}", file=sys.stderr)
        return 2

    fast_stats = data_store.load_stats()
    fast_health = data_store.load_health()
    discovery = load_discovery()
    stats = combined_stats(fast_stats, discovery)
    health = combined_health(fast_health, discovery)
    state = monitor.load_state()

    local_now = datetime.now(monitor.DISPLAY_TZ)
    allowed_dates, start, end = report_dates(
        period,
        current=local_now.date(),
        manual=truthy_env("MANUAL_RUN"),
    )
    summary = aggregate_period(stats, allowed_dates)
    text = report_text(
        period=period,
        start=start,
        end=end,
        summary=summary,
        state=state,
        health=health,
    )

    monitor.send_message(text)
    print(f"{BRAND_NAME} {period} summary sent for {start.isoformat()}..{end.isoformat()}.")
    return 0


def self_test() -> None:
    dates, start, end = report_dates(
        "weekly",
        current=date(2026, 7, 14),
        manual=True,
    )
    assert len(dates) == 7
    assert start == date(2026, 7, 8)
    assert end == date(2026, 7, 14)

    stats = {
        "daily": {
            "2026-07-13": {
                "totals": {"wheel_posts": 2, "preliminary_sent": 1},
                "sources": {"official": {"wheel_posts": 2}},
            },
            "2026-07-14": {
                "totals": {"wheel_posts": 1, "activation_sent": 1},
                "sources": {"collector": {"wheel_posts": 1}},
            },
        }
    }
    summary = aggregate_period(stats, dates)
    assert summary["totals"]["wheel_posts"] == 3
    assert summary["top_sources"][0] == ("official", 2)
    text = report_text(
        period="weekly",
        start=start,
        end=end,
        summary=summary,
        state={
            "active_wheels": {
                "one": {"deadline": "2026-07-15T10:00:00+00:00"},
                "two": {},
            },
            "participating_wheels": {"one": {"marked_at": "now"}},
        },
        health={"sources": {"ok": {"status": "ok"}}},
    )
    assert "Повторов подавлено" not in text
    assert "Ошибок источников: 0" not in text
    assert "Публикаций с колёсами: <b>3</b>" in text
    assert "Время определено: <b>1 из 2</b>" in text
    print("daily_report period summary self-test passed")


if __name__ == "__main__":
    raise SystemExit(main())
