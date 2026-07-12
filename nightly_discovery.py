from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import monitor


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


def fetch_public_channel_page(
    username: str,
    before: int | None = None,
) -> list[monitor.Message]:
    url = f"https://t.me/s/{username}"
    if before is not None:
        url += f"?before={before}"

    response = monitor.request_with_retries(
        "GET",
        url,
        attempts=2,
        timeout=monitor.REQUEST_TIMEOUT,
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
                text="\n".join(dict.fromkeys(part for part in parts if part)),
                message_url=f"https://t.me/{source or username}/{message_id}",
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
    state.setdefault("version", 2)
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
    DISCOVERY_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
    path.write_text(
        header.rstrip() + "\n\n" + "\n".join(unique(values)) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        monitor.validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    active = unique(monitor.read_list(ACTIVE_PATH))
    active_keys = {item.casefold() for item in active}
    catalog = unique(monitor.read_list(CATALOG_PATH))
    catalog = [item for item in catalog if item.casefold() not in active_keys]

    discovery = load_discovery_state()
    monitor_state = monitor.load_state()
    mappings = monitor.load_identifier_sources()
    cutoff = monitor.now_utc() - timedelta(hours=LOOKBACK_HOURS)

    promoted: list[str] = []
    notifications = 0
    duplicate_wheels = 0
    inactive_wheels = 0
    unconfirmed_wheels = 0
    errors: list[str] = []
    monitor_state_changed = False

    for username in list(catalog):
        checked_at = monitor.now_utc().isoformat()
        try:
            messages = fetch_public_channel_history(username)
        except Exception as exc:
            errors.append(f"@{username}: {type(exc).__name__}: {exc}")
            discovery["sources"][username] = {
                "checked_at": checked_at,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
            continue

        if not messages:
            errors.append(f"@{username}: no public messages found")
            discovery["sources"][username] = {
                "checked_at": checked_at,
                "status": "empty",
            }
            continue

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

        qualified: list[tuple[monitor.Message, str, datetime | None, str]] = []
        source_inactive = 0
        source_unconfirmed = 0

        for message, link in sorted(recent_items, key=lambda item: item[0].date):
            post_key = monitor.notification_key(message, link)
            if post_key in discovery["notified_wheels"]:
                duplicate_wheels += 1
                continue

            should_notify, deadline, method, status = monitor.assess_pending_wheel(
                message, link
            )
            if not should_notify:
                # Do not globally suppress an inactive candidate: the same page may
                # become active later with the “Участвовать” button.
                if status == "inactive":
                    inactive_wheels += 1
                    source_inactive += 1
                else:
                    unconfirmed_wheels += 1
                    source_unconfirmed += 1
                continue

            if monitor.is_activation_suppressed(monitor_state, link):
                duplicate_wheels += 1
                continue

            qualified.append((message, link, deadline, method))

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

        for message, link, deadline, method in qualified:
            post_key = monitor.notification_key(message, link)
            try:
                monitor.notify_new_link(message, link, deadline, method, mappings)
            except Exception as exc:
                errors.append(
                    f"@{username} message {message.message_id}: "
                    f"notification failed: {type(exc).__name__}: {exc}"
                )
                continue

            monitor.remember_alert(monitor_state, link, deadline)
            monitor.remember_activation(monitor_state, link, deadline)
            monitor_state_changed = True
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
        "# Быстрый мониторинг: подтверждённые публичные Telegram-источники.\n"
        "# Проверяется примерно каждые 5 минут. Старые ссылки отдельно не опрашиваются.",
    )
    write_sources(
        CATALOG_PATH,
        catalog,
        "# Ночной каталог: только дополнительные Telegram-каналы-кандидаты.\n"
        "# Канал переносится в быстрый список только после подтверждения активного колеса.",
    )

    if monitor_state_changed:
        monitor.save_state(monitor_state)

    discovery["last_run_at"] = monitor.now_utc().isoformat()
    discovery["catalog_size"] = len(catalog)
    discovery["active_size"] = len(active)
    discovery["promoted"] = promoted
    discovery["notifications"] = notifications
    discovery["duplicate_wheels"] = duplicate_wheels
    discovery["inactive_wheels"] = inactive_wheels
    discovery["unconfirmed_wheels"] = unconfirmed_wheels
    discovery["error_count"] = len(errors)
    save_discovery_state(discovery)

    print(
        f"Catalog: {len(catalog)}; active: {len(active)}; "
        f"promoted: {len(promoted)}; notifications: {notifications}; "
        f"inactive: {inactive_wheels}; unconfirmed: {unconfirmed_wheels}; "
        f"duplicates: {duplicate_wheels}; errors: {len(errors)}"
    )
    for error in errors[:40]:
        print(f"WARNING {error}")

    if MANUAL_RUN:
        promoted_text = ", ".join(f"@{item}" for item in promoted) or "нет"
        monitor.send_message(
            "✅ <b>Ночная проверка завершена</b>\n\n"
            f"Кандидатов осталось: {len(catalog)}\n"
            f"Перенесено в быстрый список: {html.escape(promoted_text)}\n"
            f"Новых активных уведомлений: {notifications}\n"
            f"Неактивных колёс отброшено: {inactive_wheels}\n"
            f"Неподтверждённых отброшено: {unconfirmed_wheels}\n"
            f"Повторов подавлено: {duplicate_wheels}\n"
            f"Ошибок: {len(errors)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
