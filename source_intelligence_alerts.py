from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any

from bbvg.monitor import source_discovery

UTC = timezone.utc
REMINDER_AFTER = timedelta(hours=24)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _alert_due(entry: dict[str, Any], *, current: datetime | None = None) -> bool:
    last = _parse_time(
        entry.get("recommendation_alerted_at") or entry.get("admin_alerted_at")
    )
    if last is None:
        return True
    now = (current or datetime.now(UTC)).astimezone(UTC)
    return now - last >= REMINDER_AFTER


def wheel_candidate_rows(
    state: dict[str, Any],
    known_sources: set[str] | None = None,
    ignored_sources: set[str] | None = None,
    *,
    current: datetime | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return unresolved public wheel-bearing sources whose alert is due."""

    known = {str(value).casefold() for value in (known_sources or set())}
    ignored = {str(value).casefold() for value in (ignored_sources or set())}
    rows: list[tuple[str, dict[str, Any]]] = []
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    for key, raw in candidates.items():
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or key).strip().lstrip("@")
        source_key = source.casefold()
        if (
            not source
            or source_key.endswith("bot")
            or source_key in known
            or source_key in ignored
            or raw.get("public") is not True
            or int(raw.get("wheel_links_found", 0) or 0) <= 0
            or not _alert_due(raw, current=current)
        ):
            continue
        rows.append((source, raw))
    rows.sort(
        key=lambda item: (
            _parse_time(
                item[1].get("recommendation_alerted_at")
                or item[1].get("admin_alerted_at")
            )
            or datetime.min.replace(tzinfo=UTC),
            item[0].casefold(),
        )
    )
    return rows


def candidate_message(source: str, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    found = int(entry.get("wheel_links_found", 0) or 0)
    latest = str(entry.get("latest_wheel_at") or "не определено")
    alert_count = int(entry.get("recommendation_alert_count", 0) or 0)
    samples = entry.get("sample_wheels") if isinstance(entry.get("sample_wheels"), list) else []
    sample = next((row for row in samples if isinstance(row, dict)), {})
    identifier = str(sample.get("identifier") or "")
    message_url = str(sample.get("message_url") or "")

    title = (
        "⏰ <b>Напоминание о новом источнике</b>"
        if alert_count > 0
        else "🛰️ <b>Новый источник с колесом</b>"
    )
    lines = [
        title,
        "",
        f"Канал: <b>@{html.escape(source)}</b>",
        f"Найдено ссылок на колёса: <b>{found}</b>",
        f"Последнее найденное колесо: <code>{html.escape(latest)}</code>",
    ]
    if identifier:
        lines.append(f"Пример: <code>{html.escape(identifier)}</code>")
    lines.extend(
        [
            "",
            "Канал не добавляется автоматически. Если ничего не выбрать, "
            "напоминание будет повторено после 24 часов.",
        ]
    )

    buttons: list[list[dict[str, str]]] = [
        [{"text": "📨 Открыть канал", "url": f"https://telegram.me/{source}"}],
    ]
    if message_url:
        buttons.append([{"text": "🎡 Открыть найденный пост", "url": message_url}])
    buttons.extend(
        [
            [
                {
                    "text": "➕ Добавить в источники",
                    "callback_data": f"intel:mode:fast:{source}",
                }
            ],
            [
                {
                    "text": "🙈 Игнорировать",
                    "callback_data": f"intel:ignoreask:{source}",
                }
            ],
        ]
    )
    return "\n".join(lines), {"inline_keyboard": buttons}


def notify_new_candidates(module: Any, state: dict[str, Any]) -> int:
    _, known = module.known_sources()
    ignored = module.ignored_sources()
    sent = 0
    for source, entry in wheel_candidate_rows(state, known, ignored):
        text, markup = candidate_message(source, entry)
        response = module.monitor.send_message(text, reply_markup=markup)
        result = response.get("result") if isinstance(response, dict) else None
        delivered = int(result.get("sent", 0) or 0) if isinstance(result, dict) else 0
        if delivered <= 0:
            continue
        timestamp = module.now_iso()
        entry["recommendation_alerted_at"] = timestamp
        entry["admin_alerted_at"] = timestamp
        entry["admin_alert_delivery_count"] = int(
            entry.get("admin_alert_delivery_count", 0) or 0
        ) + delivered
        entry["recommendation_alert_count"] = int(
            entry.get("recommendation_alert_count", 0) or 0
        ) + 1
        sent += 1
    return sent


def run(module: Any) -> int:
    result = module.main()
    if result != 0:
        return result
    state = module.load_state()
    lifecycle_changes = source_discovery.evaluate_state(module, state)
    sent = notify_new_candidates(module, state)
    if lifecycle_changes or sent:
        module.save_state(state)
    summary = state.get("source_discovery_lifecycle", {})
    recommended = int(summary.get("recommended", 0) or 0) if isinstance(summary, dict) else 0
    print(
        f"Source intelligence lifecycle changes: {lifecycle_changes}; "
        f"recommended={recommended}; administrator alerts sent={sent}"
    )
    return 0


def self_test() -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    state = {
        "candidates": {
            "newsource": {
                "source": "NewSource",
                "public": True,
                "wheel_links_found": 2,
                "sample_wheels": [
                    {
                        "identifier": "wheel-a",
                        "message_url": "https://telegram.me/NewSource/10",
                    }
                ],
            },
            "tooearly": {
                "source": "TooEarly",
                "public": True,
                "wheel_links_found": 1,
                "recommendation_alerted_at": (now - timedelta(hours=23, minutes=59)).isoformat(),
            },
            "reminder": {
                "source": "Reminder",
                "public": True,
                "wheel_links_found": 1,
                "recommendation_alerted_at": (now - timedelta(hours=24)).isoformat(),
                "recommendation_alert_count": 1,
            },
            "configured": {
                "source": "Configured",
                "public": True,
                "wheel_links_found": 3,
            },
            "ignored": {
                "source": "Ignored",
                "public": True,
                "wheel_links_found": 2,
            },
        }
    }
    rows = wheel_candidate_rows(
        state,
        {"configured"},
        {"ignored"},
        current=now,
    )
    assert [source for source, _ in rows] == ["NewSource", "Reminder"]
    text, markup = candidate_message("NewSource", state["candidates"]["newsource"])
    assert "Новый источник с колесом" in text
    callbacks = [
        button.get("callback_data")
        for row in markup["inline_keyboard"]
        for button in row
        if button.get("callback_data")
    ]
    assert callbacks == [
        "intel:mode:fast:NewSource",
        "intel:ignoreask:NewSource",
    ]
    reminder_text, _ = candidate_message("Reminder", state["candidates"]["reminder"])
    assert "Напоминание" in reminder_text
    print("source intelligence alert and 24 hour reminder self-test passed")


if __name__ == "__main__":
    self_test()
