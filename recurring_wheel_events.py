from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable


def install(monitor_module: Any, base_runtime: Any) -> None:
    """Keep only current-event publications when choosing a canonical post.

    Streamers reuse the same ``/freestream/<identifier>`` URL on different
    days. The old canonicalizer selected the earliest visible publication for
    that identifier, so a new post could collapse into an already-seen old one.
    Reposts are now combined only inside the current processing window.
    """

    if getattr(monitor_module, "_bbvg_recurring_wheel_events_installed", False):
        return

    raw_fetch_all: Callable = base_runtime._original_fetch_all_sources

    def fetch_all_sources_current_events(sources):
        messages_by_source, source_errors, empty_sources = raw_fetch_all(sources)
        all_candidates: dict[str, list[Any]] = {}
        recent_candidates: dict[str, list[Any]] = {}
        window = timedelta(minutes=monitor_module.MAX_NEW_POST_AGE_MINUTES)

        for messages in messages_by_source.values():
            for message in messages:
                for link in monitor_module.extract_links(message.text):
                    key = monitor_module.wheel_key(link)
                    all_candidates.setdefault(key, []).append(message)
                    if monitor_module.message_age(message) <= window:
                        recent_candidates.setdefault(key, []).append(message)

        base_runtime._CANONICAL_MESSAGES.clear()
        base_runtime._WHEEL_PUBLICATIONS.clear()
        for key, historical_rows in all_candidates.items():
            rows = recent_candidates.get(key) or historical_rows
            if not rows:
                continue
            identifier = monitor_module.wheel_identifier(
                next(
                    link
                    for message in rows
                    for link in monitor_module.extract_links(message.text)
                    if monitor_module.wheel_key(link) == key
                )
            )
            canonical = min(
                rows,
                key=lambda message: base_runtime._message_rank(message, identifier),
            )
            base_runtime._CANONICAL_MESSAGES[key] = canonical

            publications: dict[tuple[str, int], dict[str, Any]] = {}
            for row in rows:
                marker = (row.source.casefold(), row.message_id)
                publications[marker] = {
                    "source": row.source,
                    "message_id": row.message_id,
                    "message_date": row.date.astimezone(monitor_module.UTC).isoformat(),
                    "message_url": row.message_url,
                }
            base_runtime._WHEEL_PUBLICATIONS[key] = sorted(
                publications.values(),
                key=lambda item: (
                    str(item.get("message_date") or ""),
                    str(item.get("source") or "").casefold(),
                ),
            )

        for source, messages in list(messages_by_source.items()):
            rewritten: list[Any] = []
            seen_messages: set[tuple[str, int]] = set()
            for message in messages:
                wheel_keys = {
                    monitor_module.wheel_key(link)
                    for link in monitor_module.extract_links(message.text)
                }
                canonical = (
                    base_runtime._CANONICAL_MESSAGES.get(next(iter(wheel_keys)))
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

    base_runtime.fetch_all_sources_with_originals = fetch_all_sources_current_events
    monitor_module.fetch_all_sources = fetch_all_sources_current_events
    monitor_module._bbvg_recurring_wheel_events_installed = True


def self_test() -> None:
    from datetime import timedelta

    import monitor_entry as base_runtime

    monitor = base_runtime.monitor
    old_fetch = base_runtime._original_fetch_all_sources
    old_monitor_fetch = monitor.fetch_all_sources
    old_canonical = dict(base_runtime._CANONICAL_MESSAGES)
    old_publications = dict(base_runtime._WHEEL_PUBLICATIONS)
    old_flag = getattr(monitor, "_bbvg_recurring_wheel_events_installed", False)

    now = monitor.now_utc()
    old_message = monitor.Message(
        source="jestercast",
        message_id=1510,
        date=now - timedelta(days=2),
        text="https://betboom.ru/freestream/cct1",
        message_url="https://telegram.me/jestercast/1510",
    )
    current_message = monitor.Message(
        source="jestercast",
        message_id=1516,
        date=now - timedelta(hours=2),
        text="https://betboom.ru/freestream/cct1",
        message_url="https://telegram.me/jestercast/1516",
    )

    try:
        if old_flag:
            delattr(monitor, "_bbvg_recurring_wheel_events_installed")
        base_runtime._original_fetch_all_sources = lambda sources: (
            {"jestercast": [old_message, current_message]},
            {},
            [],
        )
        install(monitor, base_runtime)
        result, errors, empty = monitor.fetch_all_sources(["jestercast"])
        assert not errors and not empty
        assert [message.message_id for message in result["jestercast"]] == [1516]
        assert base_runtime._CANONICAL_MESSAGES["cct1"].message_id == 1516
        assert [row["message_id"] for row in base_runtime._WHEEL_PUBLICATIONS["cct1"]] == [1516]
    finally:
        base_runtime._original_fetch_all_sources = old_fetch
        monitor.fetch_all_sources = old_monitor_fetch
        base_runtime._CANONICAL_MESSAGES.clear()
        base_runtime._CANONICAL_MESSAGES.update(old_canonical)
        base_runtime._WHEEL_PUBLICATIONS.clear()
        base_runtime._WHEEL_PUBLICATIONS.update(old_publications)
        if old_flag:
            monitor._bbvg_recurring_wheel_events_installed = True
        elif hasattr(monitor, "_bbvg_recurring_wheel_events_installed"):
            delattr(monitor, "_bbvg_recurring_wheel_events_installed")

    print("recurring_wheel_events current-event canonicalization self-test passed")


if __name__ == "__main__":
    self_test()
