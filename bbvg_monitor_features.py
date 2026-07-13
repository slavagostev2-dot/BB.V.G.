from __future__ import annotations

from typing import Any

import notification_router_v2
import source_registry
import source_reputation

_RATING = source_reputation.load()
_COMMON_INSTALLED = False
_FAST_INSTALLED = False


def install_common(monitor_module: Any) -> None:
    global _COMMON_INSTALLED
    notification_router_v2.install(monitor_module)
    if _COMMON_INSTALLED:
        return

    original_notification_key = monitor_module.notification_key

    def notification_key_with_publication(message, link):
        key = original_notification_key(message, link)
        try:
            deadline, _ = monitor_module.infer_deadline(message.text, message.date)
            source_reputation.record_publication(
                _RATING,
                source=message.source,
                message_id=message.message_id,
                published_at=message.date.astimezone(monitor_module.UTC).isoformat(),
                message_url=message.message_url,
                wheel_url=monitor_module.normalize_url(link),
                wheel_key=monitor_module.wheel_key(link),
                inferred_deadline=deadline.isoformat() if deadline else None,
            )
        except Exception as exc:
            print(
                "WARNING publication history: "
                f"{type(exc).__name__}: {exc}"
            )
        return key

    monitor_module.notification_key = notification_key_with_publication
    _COMMON_INSTALLED = True


def flush(
    *,
    source_stats: dict | None = None,
    discovery_stats: dict | None = None,
) -> None:
    try:
        source_reputation.sync_automatic_stats(
            _RATING,
            source_stats or {},
            discovery_stats or {},
        )
        source_reputation.save(_RATING)
    except Exception as exc:
        print(f"WARNING source reputation save: {type(exc).__name__}: {exc}")
    try:
        source_registry.save_snapshot()
    except Exception as exc:
        print(f"WARNING source registry save: {type(exc).__name__}: {exc}")


def install_fast(runtime_module: Any) -> None:
    global _FAST_INSTALLED
    monitor = runtime_module.monitor
    install_common(monitor)
    if _FAST_INSTALLED:
        return

    base_runtime = runtime_module.base_runtime
    raw_fetch = base_runtime._original_fetch_all_sources

    def fetch_all_sources_preserving_publications(sources):
        messages_by_source, source_errors, empty_sources = raw_fetch(sources)
        candidates: dict[str, list[Any]] = {}
        for messages in messages_by_source.values():
            for message in messages:
                for link in monitor.extract_links(message.text):
                    candidates.setdefault(monitor.wheel_key(link), []).append(message)

        base_runtime._CANONICAL_MESSAGES.clear()
        for key, rows in candidates.items():
            if not rows:
                continue
            first_link = next(
                (
                    link
                    for message in rows
                    for link in monitor.extract_links(message.text)
                    if monitor.wheel_key(link) == key
                ),
                "",
            )
            if not first_link:
                continue
            identifier = monitor.wheel_identifier(first_link)
            base_runtime._CANONICAL_MESSAGES[key] = min(
                rows,
                key=lambda message: base_runtime._message_rank(message, identifier),
            )

        # Raw posts stay attached to their real Telegram source. Canonical
        # selection is used only by assessment/notification functions.
        return messages_by_source, source_errors, empty_sources

    monitor.fetch_all_sources = fetch_all_sources_preserving_publications

    original_process_active = monitor.process_active_wheels

    def process_only_admin_confirmed(state: dict, stats: dict):
        active = state.setdefault("active_wheels", {})
        waiting = {
            key: value
            for key, value in list(active.items())
            if isinstance(value, dict)
            and str(value.get("admin_verdict") or "") != "active"
        }
        for key in waiting:
            active.pop(key, None)
        try:
            result = original_process_active(state, stats)
        finally:
            for key, value in waiting.items():
                if key not in state.setdefault("inactive_wheels", {}):
                    active.setdefault(key, value)
        result["awaiting_admin"] = len(waiting)
        return result

    monitor.process_active_wheels = process_only_admin_confirmed

    original_remember = monitor.remember_pending

    def remember_with_admin_status(*args, **kwargs):
        result = original_remember(*args, **kwargs)
        state = args[0] if args else kwargs.get("state")
        link = args[3] if len(args) > 3 else kwargs.get("link")
        if isinstance(state, dict) and link:
            key = monitor.wheel_key(link)
            entry = state.setdefault("active_wheels", {}).get(key)
            if isinstance(entry, dict):
                verdict = str(
                    state.get("admin_verdicts", {}).get(key, {}).get("status")
                    or entry.get("admin_verdict")
                    or ""
                )
                if verdict == "active":
                    entry["admin_verdict"] = "active"
                    entry["status"] = "confirmed_by_admin"
                else:
                    entry["admin_verdict"] = "pending"
                    entry["status"] = "awaiting_admin"
        return result

    monitor.remember_pending = remember_with_admin_status

    original_save_stats = monitor.data_store.save_stats

    def save_stats_with_reputation(stats):
        original_save_stats(stats)
        discovery = {}
        try:
            discovery = monitor.data_store._load_json(
                monitor.data_store.DISCOVERY_STATE_PATH, {}
            )
        except Exception:
            discovery = {}
        flush(
            source_stats=stats.get("sources", {}) if isinstance(stats, dict) else {},
            discovery_stats=(
                discovery.get("stats_sources", {})
                if isinstance(discovery, dict)
                else {}
            ),
        )

    monitor.data_store.save_stats = save_stats_with_reputation
    _FAST_INSTALLED = True


def self_test() -> None:
    assert isinstance(_RATING, dict)
    print("BB V.G. monitor features self-test passed")


if __name__ == "__main__":
    self_test()
