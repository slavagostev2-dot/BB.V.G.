from __future__ import annotations

import html
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta

import monitor
import monitor_data as data_store


BRAND_NAME = "BB V.G."


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


def main() -> int:
    try:
        monitor.validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    fast_stats = data_store.load_stats()
    fast_health = data_store.load_health()
    fast_samples = data_store.load_unknown_samples()
    discovery = load_discovery()
    stats = combined_stats(fast_stats, discovery)
    health = combined_health(fast_health, discovery)

    local_now = datetime.now(monitor.DISPLAY_TZ)
    report_day = (local_now.date() - timedelta(days=1)).isoformat()

    fast = data_store.operational_sources(
        monitor.read_list(monitor.SOURCES_PATH), "fast"
    )
    nightly = data_store.operational_sources(
        monitor.read_list(monitor.ROOT / "source_catalog.txt"), "nightly"
    )
    daily = stats.get("daily", {}).get(report_day, {})
    totals = daily.get("totals", {}) if isinstance(daily, dict) else {}

    health_entries = [
        entry
        for entry in health.get("sources", {}).values()
        if isinstance(entry, dict)
    ]
    healthy_count = sum(1 for entry in health_entries if entry.get("status") == "ok")
    quarantined = [
        username
        for username, entry in health.get("sources", {}).items()
        if isinstance(entry, dict) and entry.get("status") == "quarantined"
    ]
    problem_count = sum(
        1
        for entry in health_entries
        if entry.get("status") not in {"ok", "quarantined"}
    )
    unavailable = data_store.unavailable_sources(health)
    unknown_count = len(fast_samples.get("samples", [])) + len(
        discovery.get("unknown_timer_samples", [])
    )

    top_lines: list[str] = []
    for source, _, entry in data_store.top_sources(stats, 5):
        top_lines.append(
            f"• @{html.escape(source)} — постов {counter(entry, 'wheel_posts')}, "
            f"активаций {counter(entry, 'activation_sent')}"
        )
    if not top_lines:
        top_lines.append("• статистика ещё накапливается")

    unavailable_lines: list[str] = []
    for source, entry, days in unavailable[:8]:
        reason = str(entry.get("last_error") or entry.get("status") or "недоступен")
        unavailable_lines.append(
            f"• @{html.escape(source)} — {days} дн., {html.escape(reason[:80])}"
        )
    if not unavailable_lines:
        unavailable_lines.append("• длительно недоступных каналов нет")

    text = (
        f"📊 <b>Ежедневный отчёт {BRAND_NAME} — {report_day}</b>\n\n"
        f"<b>Источники</b>\n"
        f"Быстрая проверка: {len(fast)}\n"
        f"Ночная проверка: {len(nightly)}\n"
        f"Доступны по последней проверке: {healthy_count}\n"
        f"С временными проблемами: {problem_count}\n"
        f"В карантине: {len(quarantined)}\n\n"
        f"<b>За отчётный день</b>\n"
        f"Проверок: {counter(totals, 'checks')}\n"
        f"Новых постов с колёсами: {counter(totals, 'wheel_posts')}\n"
        f"Отправлено уведомлений: {counter(totals, 'preliminary_sent')}\n"
        f"Колёс с найденным временем: {counter(totals, 'activation_sent')}\n"
        f"Повторов подавлено: {counter(totals, 'duplicates_suppressed')}\n"
        f"Ошибок источников: {counter(totals, 'errors')}\n"
        f"Неизвестных форматов времени в базе: {unknown_count}\n\n"
        f"<b>Где чаще появляются колёса</b>\n"
        + "\n".join(top_lines)
        + "\n\n<b>Недоступны несколько дней</b>\n"
        + "\n".join(unavailable_lines)
    )

    monitor.send_message(text)
    print(f"{BRAND_NAME} daily report sent for {report_day}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
