from __future__ import annotations

import html
import json
from datetime import datetime, timedelta

import monitor
import notification_router

notification_router.install(monitor)

_original_assess_new = monitor.assess_new_wheel
_original_assess_pending = monitor.assess_pending_wheel
_original_fetch_all_sources = monitor.fetch_all_sources
_original_mark_participating = monitor.mark_participating
_original_process_active_wheels = monitor.process_active_wheels

_IDENTIFIER_MAPPINGS = monitor.load_identifier_sources()
try:
    _identifier_config = json.loads(
        monitor.IDENTIFIER_SOURCES_PATH.read_text(encoding="utf-8")
    )
except (OSError, json.JSONDecodeError):
    _identifier_config = {}
_COLLECTOR_SOURCES = {
    str(value).strip().lstrip("@").casefold()
    for value in _identifier_config.get("collectors", [])
    if str(value).strip()
}
_CANONICAL_MESSAGES: dict[str, monitor.Message] = {}
_WHEEL_PUBLICATIONS: dict[str, list[dict]] = {}


def _mapped_sources(identifier: str) -> set[str]:
    return {
        value.casefold()
        for value in monitor.related_sources(identifier, _IDENTIFIER_MAPPINGS)
    }


def _source_rank(identifier: str, source: str) -> int:
    normalized = source.strip().lstrip("@").casefold()
    if normalized in _mapped_sources(identifier):
        return 0
    if normalized in _COLLECTOR_SOURCES:
        return 2
    return 1


def _message_rank(message: monitor.Message, identifier: str) -> tuple:
    return (
        _source_rank(identifier, message.source),
        message.date.astimezone(monitor.UTC),
        message.message_id,
    )


def fetch_all_sources_with_originals(sources):
    """Use the mapped creator or earliest non-collector post as wheel origin."""
    messages_by_source, source_errors, empty_sources = _original_fetch_all_sources(sources)
    candidates: dict[str, list[monitor.Message]] = {}

    for messages in messages_by_source.values():
        for message in messages:
            for link in monitor.extract_links(message.text):
                candidates.setdefault(monitor.wheel_key(link), []).append(message)

    _CANONICAL_MESSAGES.clear()
    _WHEEL_PUBLICATIONS.clear()
    for key, rows in candidates.items():
        if not rows:
            continue
        identifier = monitor.wheel_identifier(
            next(
                link
                for message in rows
                for link in monitor.extract_links(message.text)
                if monitor.wheel_key(link) == key
            )
        )
        _CANONICAL_MESSAGES[key] = min(
            rows,
            key=lambda message: _message_rank(message, identifier),
        )
        publications: dict[tuple[str, int], dict] = {}
        for row in rows:
            marker = (row.source.casefold(), row.message_id)
            publications[marker] = {
                "source": row.source,
                "message_id": row.message_id,
                "message_date": row.date.astimezone(monitor.UTC).isoformat(),
                "message_url": row.message_url,
            }
        _WHEEL_PUBLICATIONS[key] = sorted(
            publications.values(),
            key=lambda item: (str(item.get("message_date") or ""), str(item.get("source") or "").casefold()),
        )

    # Most wheel posts contain one identifier. Replacing reposts with the
    # canonical message makes deadline inference, source attribution and
    # duplicate keys consistently use the original publication.
    for source, messages in list(messages_by_source.items()):
        rewritten: list[monitor.Message] = []
        seen_messages: set[tuple[str, int]] = set()
        for message in messages:
            wheel_keys = {
                monitor.wheel_key(link)
                for link in monitor.extract_links(message.text)
            }
            canonical = (
                _CANONICAL_MESSAGES.get(next(iter(wheel_keys)))
                if len(wheel_keys) == 1
                else None
            )
            selected = canonical or message
            marker = (selected.source.casefold(), selected.message_id)
            if marker in seen_messages:
                continue
            seen_messages.add(marker)
            rewritten.append(selected)
        messages_by_source[source] = rewritten

    return messages_by_source, source_errors, empty_sources


def _notification_first(message, result):
    """Deliver a fresh unique Telegram wheel post even if page parsing is inconclusive."""
    age = monitor.message_age(message)
    if result.should_notify:
        return result
    if age <= timedelta(minutes=monitor.MAX_NEW_POST_AGE_MINUTES):
        return monitor.WheelAssessment(
            True,
            result.deadline,
            f"новая уникальная публикация; {result.method}",
            "preliminary",
            result.page_excerpt,
        )
    return result


def assess_new_notification_first(message, link, state=None):
    canonical = _CANONICAL_MESSAGES.get(monitor.wheel_key(link), message)
    return _notification_first(
        canonical,
        _original_assess_new(canonical, link, state),
    )


def assess_pending_notification_first(message, link, state=None):
    canonical = _CANONICAL_MESSAGES.get(monitor.wheel_key(link), message)
    return _notification_first(
        canonical,
        _original_assess_pending(canonical, link, state),
    )


def _deadline_from_record(record):
    if not isinstance(record, dict):
        return None
    direct = monitor.parse_datetime(record.get("deadline"))
    if direct:
        return direct
    published = monitor.parse_datetime(record.get("message_date"))
    text = str(record.get("message_text") or "")
    if not published or not text:
        return None
    deadline, _ = monitor.infer_deadline(text, published)
    return deadline


def _record_wheel_key(record):
    if not isinstance(record, dict):
        return ""
    key = str(record.get("wheel_key") or "").casefold()
    if key:
        return key
    url = str(record.get("url") or "")
    return monitor.wheel_key(url) if url else ""


def _record_time(record: dict) -> datetime:
    return (
        monitor.parse_datetime(record.get("message_date"))
        or monitor.parse_datetime(record.get("created_at"))
        or monitor.parse_datetime(record.get("first_seen_at"))
        or monitor.parse_datetime(record.get("first_notified_at"))
        or datetime.max.replace(tzinfo=monitor.UTC)
    )


def _best_origin_record(state: dict, key: str, entry: dict) -> dict:
    identifier = str(entry.get("identifier") or key)
    rows: list[dict] = [entry]
    participant = state.get("participating_wheels", {}).get(key)
    if isinstance(participant, dict) and _record_wheel_key(participant) in {"", key}:
        rows.append(participant)
    for collection_name in ("button_contexts", "pending_posts"):
        collection = state.get(collection_name, {})
        if not isinstance(collection, dict):
            continue
        for record in collection.values():
            if isinstance(record, dict) and _record_wheel_key(record) == key:
                rows.append(record)

    useful = [
        record
        for record in rows
        if str(record.get("source") or "")
        and (record.get("message_text") or record.get("message_date"))
    ]
    if not useful:
        return entry
    return min(
        useful,
        key=lambda record: (
            _source_rank(identifier, str(record.get("source") or "")),
            _record_time(record),
            int(record.get("message_id", 0) or 0),
        ),
    )


def _apply_origin(entry: dict, origin: dict) -> bool:
    changed = False
    for field in (
        "source",
        "message_id",
        "message_date",
        "message_url",
        "message_text",
    ):
        value = origin.get(field)
        if value not in (None, "") and entry.get(field) != value:
            entry[field] = value
            changed = True
    origin_method = str(origin.get("method") or "")
    if origin_method and _deadline_from_record(origin):
        if entry.get("method") != origin_method:
            entry["method"] = origin_method[:300]
            changed = True
    return changed


def _persist_publications(state: dict, key: str, fallback: dict | None = None) -> None:
    rows = list(_WHEEL_PUBLICATIONS.get(key, []))
    if not rows and isinstance(fallback, dict) and fallback.get("source"):
        rows = [{
            "source": str(fallback.get("source") or ""),
            "message_id": int(fallback.get("message_id", 0) or 0),
            "message_date": str(fallback.get("message_date") or fallback.get("created_at") or ""),
            "message_url": str(fallback.get("message_url") or ""),
        }]
    if rows:
        state.setdefault("wheel_publications", {})[key] = rows


def _recover_deadline(state, key, entry):
    origin = _best_origin_record(state, key, entry)
    deadline = _deadline_from_record(origin)
    if deadline:
        return deadline

    direct = _deadline_from_record(entry)
    if direct:
        return direct

    participant = state.get("participating_wheels", {}).get(key)
    return _deadline_from_record(participant)


def mark_participating_with_tracking(state, context):
    """Keep a participated wheel tracked even when its page timer is unavailable."""
    _original_mark_participating(state, context)

    key = _record_wheel_key(context)
    if not key:
        return
    _persist_publications(state, key, context)
    current = monitor.now_utc()
    canonical = _CANONICAL_MESSAGES.get(key)
    if canonical is not None:
        context = dict(context)
        context.update(
            {
                "source": canonical.source,
                "message_id": canonical.message_id,
                "message_date": canonical.date.astimezone(monitor.UTC).isoformat(),
                "message_url": canonical.message_url,
                "message_text": canonical.text[:4000],
            }
        )

    deadline = _deadline_from_record(context)
    url = str(context.get("url") or "")
    participant = state.setdefault("participating_wheels", {}).get(key)
    if isinstance(participant, dict) and deadline:
        participant["deadline"] = deadline.isoformat()
        participant["expires_at"] = monitor.participation_expiry(
            deadline, current=current
        ).isoformat()

    active = state.setdefault("active_wheels", {})
    entry = active.get(key)
    if not isinstance(entry, dict):
        created_at = monitor.parse_datetime(context.get("created_at")) or current
        message_date = monitor.parse_datetime(context.get("message_date")) or created_at
        try:
            message_id = int(context.get("message_id", 0) or 0)
        except (TypeError, ValueError):
            message_id = 0
        entry = {
            "identifier": str(context.get("identifier") or key),
            "url": monitor.normalize_url(url) if url else "",
            "source": str(context.get("source") or "неизвестно"),
            "message_id": message_id,
            "message_date": message_date.isoformat(),
            "message_url": str(context.get("message_url") or ""),
            "message_text": str(context.get("message_text") or "")[:4000],
            "status": str(context.get("status") or "tracked"),
            "method": str(context.get("method") or "участие отмечено")[:300],
            "page_excerpt": str(context.get("page_excerpt") or "")[:1200],
            "first_notified_at": created_at.isoformat(),
            "last_notification_at": created_at.isoformat(),
            "last_checked_at": current.isoformat(),
            "expires_at": monitor.participation_expiry(
                deadline, current=current
            ).isoformat(),
            "participating": True,
            "participating_at": current.isoformat(),
        }
        active[key] = entry
    else:
        entry["participating"] = True
        entry["participating_at"] = current.isoformat()
        _apply_origin(entry, context)

    if deadline and not monitor.parse_datetime(entry.get("deadline")):
        entry["deadline"] = deadline.isoformat()
        entry["expires_at"] = monitor.participation_expiry(
            deadline, current=current
        ).isoformat()


def process_active_wheels_with_draw_alert(state, stats):
    """Send one alert using the original source and its original publication time."""
    current = monitor.now_utc()
    sent = state.setdefault("completed_wheel_alerts", {})
    changed = False
    alerts_sent = 0

    for key, entry in list(state.setdefault("active_wheels", {}).items()):
        if not isinstance(entry, dict):
            continue
        normalized = str(key).casefold()
        origin = _best_origin_record(state, normalized, entry)
        if _apply_origin(entry, origin):
            changed = True

        deadline = _recover_deadline(state, normalized, entry)
        if deadline and monitor.parse_datetime(entry.get("deadline")) != deadline:
            entry["deadline"] = deadline.isoformat()
            entry["expires_at"] = monitor.participation_expiry(
                deadline, current=current
            ).isoformat()
            participant = state.get("participating_wheels", {}).get(normalized)
            if isinstance(participant, dict):
                participant["deadline"] = deadline.isoformat()
                participant["expires_at"] = monitor.participation_expiry(
                    deadline, current=current
                ).isoformat()
            changed = True

        if not deadline or current < deadline or normalized in sent:
            continue

        identifier = str(entry.get("identifier") or key)
        source = str(entry.get("source") or "неизвестно")
        url = str(entry.get("url") or "")
        markup = (
            {"inline_keyboard": [[{"text": "🎡 Открыть колесо", "url": url}]]}
            if url
            else None
        )
        try:
            monitor.send_message(
                "🎯 <b>Время прокрутки колеса наступило</b>\n\n"
                f"Идентификатор: <code>{html.escape(identifier)}</code>\n"
                f"Оригинальный источник: @{html.escape(source)}\n"
                "Статус участия хранится отдельно для каждого пользователя.\n\n"
                "Колесо уже должно быть прокручено. Откройте страницу и проверьте результат.",
                reply_markup=markup,
            )
        except Exception as exc:
            entry["draw_alert_error"] = f"{type(exc).__name__}: {exc}"[:300]
            changed = True
        else:
            sent[normalized] = {
                "identifier": identifier,
                "source": source,
                "url": url,
                "deadline": deadline.isoformat(),
                "notified_at": current.isoformat(),
            }
            entry["draw_alert_sent_at"] = current.isoformat()
            monitor.data_store.increment_stat(stats, source, "draw_time_alerts")
            alerts_sent += 1
            changed = True

    result = _original_process_active_wheels(state, stats)
    result["draw_alerts"] = alerts_sent
    if changed:
        result["changed"] = True
    return result


monitor.fetch_all_sources = fetch_all_sources_with_originals
monitor.assess_new_wheel = assess_new_notification_first
monitor.assess_pending_wheel = assess_pending_notification_first
monitor.mark_participating = mark_participating_with_tracking
monitor.process_active_wheels = process_active_wheels_with_draw_alert

if __name__ == "__main__":
    raise SystemExit(monitor.main())
