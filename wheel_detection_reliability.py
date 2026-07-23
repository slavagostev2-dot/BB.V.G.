from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable

DISCOVERED_WHEEL_TITLE = "🎡 <b>Обнаружено колесо BetBoom</b>"
LEGACY_NEW_WHEEL_TITLE = "🎡 <b>Новое колесо BetBoom</b>"
AUTO_PARTICIPATION_BUTTON_TEXT = "🤖 Автоучастие подтверждено"
CREATOR_BACKFILL_MIN_MESSAGES = max(
    2, int(os.getenv("CREATOR_BACKFILL_MIN_MESSAGES", "15"))
)
CREATOR_BACKFILL_COOLDOWN_SECONDS = max(
    15, int(os.getenv("CREATOR_BACKFILL_COOLDOWN_SECONDS", "60"))
)

_BACKFILL_LAST_AT: dict[str, float] = {}


def _clean_source(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _source_policy(monitor_module: Any) -> tuple[set[str], set[str]]:
    try:
        payload = json.loads(
            monitor_module.IDENTIFIER_SOURCES_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        payload = {}

    creators: set[str] = set()
    mappings = payload.get("mappings") if isinstance(payload, dict) else None
    if isinstance(mappings, list):
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            sources = mapping.get("sources")
            if not isinstance(sources, list):
                continue
            creators.update(
                source
                for raw in sources
                if (source := _clean_source(raw))
            )

    collectors = {
        source
        for raw in (payload.get("collectors", []) if isinstance(payload, dict) else [])
        if (source := _clean_source(raw))
    }
    return creators - collectors, collectors


def _message_key(message: Any) -> tuple[str, int]:
    source = _clean_source(getattr(message, "source", ""))
    try:
        message_id = int(getattr(message, "message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    return source, message_id


def merge_message_pages(current: list[Any], older: list[Any]) -> list[Any]:
    """Merge an overlap page without duplicating Telegram messages."""

    merged: dict[tuple[str, int], Any] = {}
    for message in older:
        merged[_message_key(message)] = message
    for message in current:
        merged[_message_key(message)] = message
    return sorted(
        merged.values(),
        key=lambda message: (
            getattr(message, "date", None),
            _clean_source(getattr(message, "source", "")),
            _message_key(message)[1],
        ),
    )


def install_creator_overlap(
    monitor_module: Any,
    monitor_entry_module: Any,
    *,
    creator_sources: set[str] | None = None,
    collector_sources: set[str] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Read one older Telegram page for busy creator channels.

    Telegram's public preview exposes a short moving window. A post can disappear
    from that window before a cached preview refreshes. The overlap page is fetched
    only for mapped creator channels, never for collector channels, and at most once
    per cooldown interval.
    """

    if getattr(monitor_entry_module, "_bbvg_creator_overlap_installed", False):
        return
    original: Callable = monitor_entry_module._original_fetch_all_sources
    configured_creators, configured_collectors = _source_policy(monitor_module)
    creators = {
        _clean_source(source)
        for source in (creator_sources if creator_sources is not None else configured_creators)
        if _clean_source(source)
    }
    collectors = {
        _clean_source(source)
        for source in (collector_sources if collector_sources is not None else configured_collectors)
        if _clean_source(source)
    }
    creators.difference_update(collectors)

    def fetch_all_with_creator_overlap(sources: list[str]):
        results, errors, empty = original(sources)
        current_time = monotonic()
        for source in sources:
            normalized = _clean_source(source)
            if normalized not in creators or normalized in collectors:
                continue
            messages = results.get(source)
            if not isinstance(messages, list) or len(messages) < CREATOR_BACKFILL_MIN_MESSAGES:
                continue
            previous = _BACKFILL_LAST_AT.get(normalized)
            if (
                previous is not None
                and current_time - previous < CREATOR_BACKFILL_COOLDOWN_SECONDS
            ):
                continue
            message_ids = [
                _message_key(message)[1]
                for message in messages
                if _message_key(message)[1] > 0
            ]
            if not message_ids:
                continue
            _BACKFILL_LAST_AT[normalized] = current_time
            try:
                older = monitor_module.fetch_public_channel(
                    source, before=min(message_ids)
                )
            except Exception as exc:
                print(
                    "WARNING creator Telegram overlap failed: "
                    f"@{source} {type(exc).__name__}: {exc}"
                )
                continue
            if older:
                results[source] = merge_message_pages(messages, list(older))
                if source in empty:
                    empty.remove(source)
        return results, errors, empty

    monitor_entry_module._original_fetch_all_sources = fetch_all_with_creator_overlap
    monitor_entry_module._bbvg_creator_overlap_installed = True
    monitor_module._bbvg_creator_overlap_installed = True


def clarify_notification_text(text: str) -> str:
    return str(text or "").replace(LEGACY_NEW_WHEEL_TITLE, DISCOVERED_WHEEL_TITLE)


def _automatic_marker(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    source = str(record.get("participation_source") or "").strip().casefold()
    if source and any(
        marker in source
        for marker in ("auto", "betboom", "browser", "recovery", "worker")
    ):
        return True
    status = str(record.get("auto_participation_status") or "").strip().casefold()
    if status in {"participated", "already_participating", "confirmed"}:
        return True
    return bool(
        record.get("auto_participation_confirmed_at")
        or record.get("bot_success_pending_at")
    )


def is_automatic_participation(
    monitor_module: Any,
    state: dict[str, Any],
    link_or_key: str,
) -> bool:
    try:
        key = (
            monitor_module.wheel_key(link_or_key)
            if "://" in str(link_or_key)
            else str(link_or_key).casefold()
        )
    except Exception:
        key = str(link_or_key).casefold()
    participant = state.get("participating_wheels", {}).get(key)
    active = state.get("active_wheels", {}).get(key)
    return _automatic_marker(participant) or _automatic_marker(active)


def clarify_participation_markup(
    monitor_module: Any,
    state: dict[str, Any],
    link: str,
    markup: dict[str, Any],
) -> dict[str, Any]:
    if not is_automatic_participation(monitor_module, state, link):
        return markup
    for row in markup.get("inline_keyboard", []):
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, dict):
                continue
            callback = str(button.get("callback_data") or "")
            if callback.startswith(("bb:n:", "wheel:part:")) or str(
                button.get("text") or ""
            ) == "✅ Участие отмечено":
                button["text"] = AUTO_PARTICIPATION_BUTTON_TEXT
    return markup


def install_notification_clarity(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_notification_clarity_installed", False):
        return
    original_send: Callable = monitor_module.send_message
    original_markup: Callable = monitor_module.wheel_reply_markup

    def send_with_clear_discovery_title(text: str, url=None, reply_markup=None):
        return original_send(
            clarify_notification_text(text),
            url=url,
            reply_markup=reply_markup,
        )

    def markup_with_auto_participation_label(state, message, link, **kwargs):
        markup = original_markup(state, message, link, **kwargs)
        return clarify_participation_markup(monitor_module, state, link, markup)

    monitor_module.send_message = send_with_clear_discovery_title
    monitor_module.wheel_reply_markup = markup_with_auto_participation_label
    monitor_module._bbvg_notification_clarity_installed = True


def install_owner_notification_update() -> None:
    """Use an explicit automatic label when Control Center edits a sent card."""

    try:
        import auto_participation_owner_sync as owner_sync
        import notification_integrity_v2
    except ImportError:
        return
    if getattr(owner_sync, "_bbvg_auto_button_clarity_installed", False):
        return

    def mark_original_notification(panel: Any, owner_chat_id: str, item: dict[str, Any]) -> bool:
        button_token = str(item.get("button_token") or "").strip().casefold()
        if not button_token:
            return False
        try:
            message_id = notification_integrity_v2.participation_message_id(
                owner_chat_id, button_token
            )
        except Exception as exc:
            print(
                "WARNING participation notification lookup failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return False
        if not message_id:
            return False
        url = str(item.get("url") or "").strip()
        button: dict[str, Any] = {
            "text": AUTO_PARTICIPATION_BUTTON_TEXT,
            "callback_data": f"bb:n:{button_token}",
        }
        if url:
            button = {"text": AUTO_PARTICIPATION_BUTTON_TEXT, "url": url}
        try:
            response = panel.telegram_api(
                "editMessageReplyMarkup",
                {
                    "chat_id": owner_chat_id,
                    "message_id": int(message_id),
                    "reply_markup": {"inline_keyboard": [[button]]},
                },
            )
        except Exception as exc:
            print(
                "WARNING automatic participation button update failed: "
                f"message_id={message_id} {type(exc).__name__}: {exc}"
            )
            return False
        return bool(response.get("ok", True)) if isinstance(response, dict) else True

    owner_sync._mark_original_notification = mark_original_notification
    owner_sync._bbvg_auto_button_clarity_installed = True


def install(monitor_module: Any) -> None:
    install_notification_clarity(monitor_module)
    install_owner_notification_update()
    monitor_entry_module = sys.modules.get("monitor_entry")
    if monitor_entry_module is not None:
        install_creator_overlap(monitor_module, monitor_entry_module)
