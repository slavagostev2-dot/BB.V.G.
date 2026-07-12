from __future__ import annotations

import html
from datetime import datetime
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import monitor


ROOT = Path(__file__).resolve().parent
ACTIVE_PATH = ROOT / "public_sources.txt"
CATALOG_PATH = ROOT / "source_catalog.txt"
KNOWN_IDS_PATH = ROOT / "known_freestream_ids.txt"
DISCOVERY_STATE_PATH = ROOT / "discovery_state.json"

LOOKBACK_HOURS = max(12, int(os.getenv("DISCOVERY_LOOKBACK_HOURS", "36")))
HISTORY_PAGES = max(1, min(8, int(os.getenv("DISCOVERY_PAGES", "4"))))


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


def fetch_public_channel_history(
    username: str,
    pages: int,
) -> list[monitor.Message]:
    messages: dict[int, monitor.Message] = {}
    before: int | None = None

    for _ in range(max(1, pages)):
        page = fetch_public_channel_page(username, before=before)
        if not page:
            break
        for message in page:
            messages[message.message_id] = message
        next_before = min(message.message_id for message in page)
        if before == next_before:
            break
        before = next_before

    return sorted(messages.values(), key=lambda item: item.message_id)


def write_source_list(path: Path, values: list[str], header: str) -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    path.write_text(header.rstrip() + "\n" + "\n".join(unique) + "\n", encoding="utf-8")


def write_known_ids(values: list[str]) -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    KNOWN_IDS_PATH.write_text(
        "# Известные идентификаторы ссылок BetBoom /freestream/.\n"
        "# Список пополняется вручную и ночным поиском.\n"
        + "\n".join(unique)
        + "\n",
        encoding="utf-8",
    )


def load_discovery_state() -> dict:
    try:
        data = json.loads(DISCOVERY_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    return data


def save_discovery_state(data: dict) -> None:
    DISCOVERY_STATE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        monitor.validate_environment()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    active = monitor.read_list(ACTIVE_PATH)
    active_keys = {value.casefold() for value in active}
    catalog = monitor.read_list(CATALOG_PATH)
    known_ids = monitor.read_list(KNOWN_IDS_PATH)
    known_keys = {value.casefold() for value in known_ids}

    state = monitor.load_state()
    initialized = set(state["initialized_sources"])
    discovery = load_discovery_state()
    cutoff = monitor.now_utc() - timedelta(hours=LOOKBACK_HOURS)

    promoted: list[tuple[str, monitor.Message, str, int]] = []
    new_identifiers: list[str] = []
    errors: list[str] = []

    for username in catalog:
        checked_at = monitor.now_utc().isoformat()
        try:
            messages = fetch_public_channel_history(username, pages=HISTORY_PAGES)
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

        wheel_items: list[tuple[monitor.Message, str]] = []
        recent_wheel_items: list[tuple[monitor.Message, str]] = []

        for message in messages:
            for link in monitor.extract_links(message.text):
                wheel_items.append((message, link))
                if message.date >= cutoff:
                    recent_wheel_items.append((message, link))

                identifier = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
                if identifier.casefold() not in known_keys:
                    known_keys.add(identifier.casefold())
                    known_ids.append(identifier)
                    new_identifiers.append(identifier)

        latest = max(wheel_items, key=lambda item: item[0].date, default=None)
        discovery["sources"][username] = {
            "checked_at": checked_at,
            "status": "ok",
            "messages_checked": len(messages),
            "wheel_links_found": len(wheel_items),
            "recent_wheel_links": len(recent_wheel_items),
            "latest_wheel_at": latest[0].date.isoformat() if latest else None,
        }

        if username.casefold() not in active_keys and recent_wheel_items:
            active.append(username)
            active_keys.add(username.casefold())
            latest_recent = max(recent_wheel_items, key=lambda item: item[0].date)
            promoted.append((username, latest_recent[0], latest_recent[1], len(recent_wheel_items)))

            # The nightly alert below is the notification for these posts. Seed them
            # so the next five-minute run does not repeat the same links.
            for message, link in recent_wheel_items:
                key = monitor.notification_key(message, link)
                state["seen"].setdefault(key, monitor.now_utc().isoformat())
            initialized.add(username)

    state["initialized_sources"] = sorted(initialized)
    monitor.save_state(state)

    write_source_list(
        ACTIVE_PATH,
        active,
        "# Быстрый мониторинг: подтверждённые источники. Проверяется каждые 5 минут.\n"
        "# Новые источники автоматически добавляются ночным workflow.",
    )
    write_known_ids(known_ids)

    discovery["last_run_at"] = monitor.now_utc().isoformat()
    discovery["catalog_size"] = len(catalog)
    discovery["active_size"] = len(active)
    discovery["promoted"] = [item[0] for item in promoted]
    discovery["new_identifiers"] = new_identifiers
    discovery["error_count"] = len(errors)
    save_discovery_state(discovery)

    for username, message, link, count in promoted:
        identifier = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
        monitor.send_message(
            "🔎 <b>Новый источник добавлен в быстрый мониторинг</b>\n\n"
            f"Канал: <a href=\"https://t.me/{html.escape(username, quote=True)}\">"
            f"@{html.escape(username)}</a>\n"
            f"Найдено свежих публикаций: {count}\n"
            f"Последний идентификатор: <code>{html.escape(identifier)}</code>\n"
            f"Пост: <a href=\"{html.escape(message.message_url, quote=True)}\">открыть</a>\n\n"
            "Канал автоматически перенесён из ночного каталога в проверку каждые 5 минут.",
            link,
        )

    print(
        f"Catalog: {len(catalog)}; active: {len(active)}; "
        f"promoted: {len(promoted)}; new identifiers: {len(new_identifiers)}; "
        f"errors: {len(errors)}"
    )
    for error in errors[:40]:
        print(f"WARNING {error}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
