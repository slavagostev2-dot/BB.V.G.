from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import monitor
import monitor_data as data_store
import telegram_transport


ROOT = Path(__file__).resolve().parent
ACTIVE_PATH = ROOT / "public_sources.txt"
CATALOG_PATH = ROOT / "source_catalog.txt"
DISCOVERY_STATE_PATH = ROOT / "discovery_state.json"

LOOKBACK_HOURS = max(12, int(os.getenv("DISCOVERY_LOOKBACK_HOURS", "48")))
HISTORY_PAGES = max(1, min(8, int(os.getenv("DISCOVERY_PAGES", "4"))))
MANUAL_RUN = os.getenv("MANUAL_RUN", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def should_notify_completion(*, manual_run: bool, catalog_size_at_start: int) -> bool:
    """Only a real, explicitly started night scan gets a completion notice."""
    return bool(manual_run and catalog_size_at_start > 0)


def promotion_admin_message(promotions: list[dict[str, str]]) -> str:
    """Describe automatic tier promotions without routing the text to users."""
    lines = [
        "🛰️ <b>Новый источник автоматически добавлен в основную проверку</b>",
        "",
        "Причина: ночная проверка подтвердила активное колесо.",
    ]
    for item in promotions:
        source = html.escape(str(item.get("source") or "").lstrip("@"))
        identifier = html.escape(str(item.get("identifier") or "неизвестен"))
        message_url = html.escape(str(item.get("message_url") or ""), quote=True)
        lines.extend(["", f"• <b>@{source}</b>", f"  ID: <code>{identifier}</code>"])
        if message_url:
            lines.append(f'  <a href="{message_url}">Публикация в Telegram</a>')
    lines.extend(
        [
            "",
            "Канал уже перенесён из ночного списка в основной.",
        ]
    )
    return "\n".join(lines)


def fetch_public_channel_page(
    username: str,
    before: int | None = None,
    *,
    attempts: int = 2,
    timeout: int | None = None,
) -> list[monitor.Message]:
    url = telegram_transport.public_source_url(username, before)

    response = monitor.request_with_retries(
        "GET",
        url,
        attempts=max(1, attempts),
        timeout=timeout or monitor.REQUEST_TIMEOUT,
        headers={"User-Agent": monitor.USER_AGENT},
        allow_redirects=True,
    )
    response.raise_for_status()
    soup = monitor.BeautifulSoup(response.text, "html.parser")
    result: list[monitor.Message] = []

    for node in soup.select("div.tgme_widget_message[data-post]"):
        data_post = str(node.get("data-post") or "")
        if "/" not in data_post:
            continue
        source, message_id_text = data_post.rsplit("/", 1)
        try:
            message_id = int(message_id_text)
        except ValueError:
            continue

        parts: list[str] = []
        text_node = node.select_one("div.tgme_widget_message_text")
        if text_node is not None:
            parts.append(text_node.get_text("\n", strip=True))
        for anchor in node.select("a[href]"):
            href = html.unescape(str(anchor.get("href") or "")).strip()
            if href:
                parts.append(href)

        time_node = node.select_one("time[datetime]")
        try:
            date = (
                datetime.fromisoformat(str(time_node.get("datetime")))
                if time_node
                else monitor.now_utc()
            )
        except ValueError:
            date = monitor.now_utc()
        if date.tzinfo is None:
            date = date.replace(tzinfo=monitor.UTC)

        result.append(
            monitor.Message(
                source=source or username,
                message_id=message_id,
                date=date,
                text=telegram_transport.rewrite_telegram_text(
                    "\n".join(dict.fromkeys(part for part in parts if part))
                ),
                message_url=telegram_transport.public_message_url(
                    source or username, message_id
                ),
            )
        )
    return sorted(result, key=lambda item: item.message_id)


def fetch_public_channel_history(username: str) -> list[monitor.Message]:
    messages: dict[int, monitor.Message] = {}
    before: int | None = None
    for _ in range(HISTORY_PAGES):
        page = fetch_public_channel_page(username, before)
        if not page:
            break
        for message in page:
            messages[message.message_id] = message
        next_before = min(message.message_id for message in page)
        if before == next_before:
            break
        before = next_before
    return sorted(messages.values(), key=lambda item: item.message_id)


def load_discovery_state() -> dict:
    try:
        state = json.loads(DISCOVERY_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    try:
        state["version"] = max(2, int(state.get("version", 2) or 2))
    except (TypeError, ValueError):
        state["version"] = 2
    state.setdefault("sources", {})
    state.setdefault("notified_wheels", {})
    return state


def save_discovery_state(state: dict) -> None:
    cutoff = monitor.now_utc() - timedelta(days=180)
    state["notified_wheels"] = {
        key: value
        for key, value in state.get("notified_wheels", {}).items()
        if not isinstance(value, dict)
        or (parsed := monitor.parse_datetime(value.get("notified_at"))) is None
        or parsed >= cutoff
    }
    try:
        state["version"] = max(2, int(state.get("version", 2) or 2))
    except (TypeError, ValueError):
        state["version"] = 2
    data_store.atomic_write_json(DISCOVERY_STATE_PATH, state)


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip().lstrip("@")
        if not clean or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        result.append(clean)
    return result


def write_sources(path: Path, values: list[str], header: str) -> None:
    body = "\n".join(unique(values))
    data_store.atomic_write_text(
        path,
        header.rstrip() + ("\n\n" + body if body else "") + "\n",
    )


def main() -> int:
    try:
        monitor.validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    active = unique(data_store.operational_sources(monitor.read_list(ACTIVE_PATH), "fast"))
    active_keys = {item.casefold() for item in active}
    catalog = unique(data_store.operational_sources(monitor.read_list(CATALOG_PATH), "nightly"))
    catalog = [item for item in catalog if item.casefold() not in active_keys]
    catalog_size_at_start = len(catalog)

    discovery = load_discovery_state()
    if not catalog:
        discovery["catalog_size"] = 0
        discovery["last_skip_at"] = monitor.now_utc().isoformat()
        discovery["last_skip_reason"] = "no_nightly_sources"
        discovery["promoted"] = []
        discovery["intelligence_candidates_added"] = 0
        discovery["notifications"] = 0
        save_discovery_state(discovery)
        print("Nightly scan skipped: no sources in the nightly list")
        return 0

    monitor_state = monitor.load_state()
    # Nightly runtime is stored inside discovery_state.json. This avoids
    # merge conflicts with the long-running five-minute monitor, which owns
    # source_health.json, source_stats.json and unknown_timer_samples.json.
    health = {
        "version": 1,
        "sources": discovery.setdefault("health_sources", {}),
    }
    stats = {
        "version": 1,
        "sources": discovery.setdefault("stats_sources", {}),
        "daily": discovery.setdefault("stats_daily", {}),
    }
    unknown_samples = {
        "version": 1,
        "samples": discovery.setdefault("unknown_timer_samples", []),
    }
    mappings = monitor.load_identifier_sources()
    cutoff = monitor.now_utc() - timedelta(hours=LOOKBACK_HOURS)

    promoted: list[str] = []
    promotion_details: list[dict[str, str]] = []
    notifications = 0
    duplicate_wheels = 0
    inactive_wheels = 0
    unconfirmed_wheels = 0
    quarantined_skipped = 0
    unknown_samples_added = 0
    errors: list[str] = []

    for username in list(catalog):
        checked_at = monitor.now_utc().isoformat()
        if not data_store.source_due_for_check(health, username):
            quarantined_skipped += 1
            data_store.record_source_check_stats(stats, username, "quarantined_skip")
            discovery["sources"][username] = {
                "checked_at": checked_at,
                "status": "quarantined",
            }
            continue

        try:
            messages = fetch_public_channel_history(username)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            quarantined = data_store.record_source_problem(
                health, username, "error", detail
            )
            data_store.record_source_check_stats(stats, username, "error")
            errors.append(
                f"@{username}: {detail}" + ("; quarantined" if quarantined else "")
            )
            discovery["sources"][username] = {
                "checked_at": checked_at,
                "status": "quarantined" if quarantined else "error",
                "error": detail[:500],
            }
            continue

        if not messages:
            quarantined = data_store.record_source_problem(
                health, username, "empty", "no public messages found"
            )
            data_store.record_source_check_stats(stats, username, "empty")
            errors.append(
                f"@{username}: no public messages found"
                + ("; quarantined" if quarantined else "")
            )
            discovery["sources"][username] = {
                "checked_at": checked_at,
                "status": "quarantined" if quarantined else "empty",
            }
            continue

        data_store.record_source_success(health, username, len(messages))
        data_store.record_source_check_stats(stats, username, "ok", len(messages))

        wheel_items = [
            (message, link)
            for message in messages
            for link in monitor.extract_links(message.text)
        ]
        recent_items = [
            (message, link)
            for message, link in wheel_items
            if message.date >= cutoff
        ]
        latest = max(wheel_items, key=lambda item: item[0].date, default=None)

        qualified: list[
            tuple[monitor.Message, str, monitor.WheelAssessment]
        ] = []
        source_inactive = 0
        source_unconfirmed = 0

        for message, link in sorted(recent_items, key=lambda item: item[0].date):
            post_key = monitor.notification_key(message, link)
            if post_key in discovery["notified_wheels"]:
                duplicate_wheels += 1
                data_store.increment_stat(stats, username, "duplicates_suppressed")
                continue

            data_store.mark_unique_wheel_post(
                stats, username, post_key, monitor.wheel_key(link)
            )
            assessment = monitor.assess_pending_wheel(
                message, link, monitor_state
            )
            if monitor.maybe_record_unknown_sample(
                unknown_samples,
                stats,
                message,
                link,
                assessment,
                reason="nightly_discovery",
            ):
                unknown_samples_added += 1

            # Nightly promotion is deliberately stricter than the fast monitor:
            # only a page-confirmed active wheel (button/timer/manual override) can
            # move a channel into the five-minute list. A Telegram time alone is
            # recorded as unconfirmed to avoid promoting stale announcement posts.
            if not assessment.should_notify or assessment.status != "active":
                if assessment.status == "inactive":
                    inactive_wheels += 1
                    source_inactive += 1
                    data_store.increment_stat(stats, username, "inactive_checks")
                else:
                    unconfirmed_wheels += 1
                    source_unconfirmed += 1
                    data_store.increment_stat(stats, username, "unconfirmed_checks")
                continue

            if monitor.is_activation_suppressed(monitor_state, link):
                duplicate_wheels += 1
                data_store.increment_stat(stats, username, "duplicates_suppressed")
                continue

            qualified.append((message, link, assessment))

        discovery["sources"][username] = {
            "checked_at": checked_at,
            "status": "ok",
            "messages_checked": len(messages),
            "wheel_links_found": len(wheel_items),
            "recent_wheel_links": len(recent_items),
            "active_wheel_links": len(qualified),
            "inactive_wheel_links": source_inactive,
            "unconfirmed_wheel_links": source_unconfirmed,
            "latest_wheel_at": latest[0].date.isoformat() if latest else None,
        }

        if not qualified:
            continue

        if username.casefold() not in active_keys:
            active.append(username)
            active_keys.add(username.casefold())
            promoted.append(username)
            first_message, first_link, _ = qualified[0]
            promotion_details.append(
                {
                    "source": username,
                    "identifier": monitor.wheel_identifier(first_link),
                    "message_url": first_message.message_url,
                }
            )

        for message, link, assessment in qualified:
            post_key = monitor.notification_key(message, link)
            try:
                monitor.notify_new_link(
                    message,
                    link,
                    assessment.deadline,
                    assessment.method,
                    mappings,
                    None,
                    assessment.page_excerpt,
                )
            except Exception as exc:
                errors.append(
                    f"@{username} message {message.message_id}: "
                    f"notification failed: {type(exc).__name__}: {exc}"
                )
                continue

            data_store.increment_stat(stats, username, "activation_sent")
            data_store.set_stat_timestamp(stats, username, "last_activation_at")
            discovery["notified_wheels"][post_key] = {
                "identifier": monitor.wheel_identifier(link),
                "url": monitor.normalize_url(link),
                "source": username,
                "message_url": message.message_url,
                "notified_at": monitor.now_utc().isoformat(),
            }
            notifications += 1

    promoted_keys = {item.casefold() for item in promoted}
    catalog = [item for item in catalog if item.casefold() not in promoted_keys]

    write_sources(
        ACTIVE_PATH,
        active,
        "# Основной мониторинг: отобранные тематические источники в 7-дневном наблюдении.\n"
        "# Проверяется примерно каждые 5 минут через telegram.me.",
    )
    write_sources(
        CATALOG_PATH,
        catalog,
        "# Ночной мониторинг: источники, вручную одобренные администратором.\n"
        "# Автоматическое пополнение отключено; активное колесо переносит источник в основной режим.",
    )

    if promotion_details:
        try:
            monitor.send_message(promotion_admin_message(promotion_details))
        except Exception as exc:
            errors.append(
                "automatic promotion admin notification failed: "
                f"{type(exc).__name__}: {exc}"
            )

    discovery["last_run_at"] = monitor.now_utc().isoformat()
    discovery["catalog_size"] = len(catalog)
    discovery["active_size"] = len(active)
    discovery["promoted"] = promoted
    discovery["intelligence_candidates_added"] = 0
    discovery["notifications"] = notifications
    discovery["duplicate_wheels"] = duplicate_wheels
    discovery["inactive_wheels"] = inactive_wheels
    discovery["unconfirmed_wheels"] = unconfirmed_wheels
    discovery["quarantined_skipped"] = quarantined_skipped
    discovery["unknown_timer_samples_added"] = unknown_samples_added
    discovery["error_count"] = len(errors)
    data_store.prune_stats(stats)
    discovery["health_sources"] = health.get("sources", {})
    discovery["stats_sources"] = stats.get("sources", {})
    discovery["stats_daily"] = stats.get("daily", {})
    discovery["unknown_timer_samples"] = unknown_samples.get("samples", [])
    save_discovery_state(discovery)

    print(
        f"Catalog: {len(catalog)}; active: {len(active)}; "
        "intelligence added: 0 (manual-only policy); "
        f"promoted: {len(promoted)}; notifications: {notifications}; "
        f"inactive: {inactive_wheels}; unconfirmed: {unconfirmed_wheels}; "
        f"quarantined: {quarantined_skipped}; unknown samples: {unknown_samples_added}; "
        f"duplicates: {duplicate_wheels}; errors: {len(errors)}"
    )
    for error in errors[:40]:
        print(f"WARNING {error}")

    if should_notify_completion(
        manual_run=MANUAL_RUN,
        catalog_size_at_start=catalog_size_at_start,
    ):
        promoted_text = ", ".join(f"@{item}" for item in promoted) or "нет"
        monitor.send_message(
            "✅ <b>Ночная проверка завершена</b>\n\n"
            f"Кандидатов осталось: {len(catalog)}\n"
            f"Перенесено в быстрый список: {html.escape(promoted_text)}\n"
            f"Новых активных уведомлений: {notifications}\n"
            f"Неактивных колёс отброшено: {inactive_wheels}\n"
            f"Неподтверждённых отброшено: {unconfirmed_wheels}\n"
            f"Источников в карантине: {quarantined_skipped}\n"
            f"Повторов подавлено: {duplicate_wheels}\n"
            f"Ошибок: {len(errors)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
