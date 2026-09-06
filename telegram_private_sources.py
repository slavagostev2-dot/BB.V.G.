from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DEFAULT_LIMIT = 40
MAX_LIMIT = 100
AUTH_PUBLIC_FALLBACK_MAX_SOURCES = max(
    1,
    min(20, int(os.getenv("TELEGRAM_AUTH_PUBLIC_FALLBACK_MAX_SOURCES", "8"))),
)


@dataclass(frozen=True)
class PrivateSource:
    alias: str
    peer_id: int


def _bare_channel_id(value: object) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError("private Telegram peer_id is empty")
    try:
        parsed = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("private Telegram peer_id must be numeric") from exc
    digits = str(abs(parsed))
    if digits.startswith("100") and len(digits) > 3:
        digits = digits[3:]
    result = int(digits)
    if result <= 0:
        raise ValueError("private Telegram peer_id must be positive after normalization")
    return result


def parse_sources(raw: str | None = None) -> list[PrivateSource]:
    value = os.getenv("TELEGRAM_PRIVATE_SOURCES_JSON", "") if raw is None else raw
    if not str(value or "").strip():
        return []
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError("TELEGRAM_PRIVATE_SOURCES_JSON is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("TELEGRAM_PRIVATE_SOURCES_JSON must be a JSON object")

    result: list[PrivateSource] = []
    seen: set[str] = set()
    for raw_alias, raw_config in payload.items():
        alias = str(raw_alias or "").strip().lstrip("@")
        if not ALIAS_RE.fullmatch(alias):
            raise ValueError(
                "private Telegram source alias must contain only letters, digits, _, . or -"
            )
        if isinstance(raw_config, dict):
            peer_value = raw_config.get("peer_id", raw_config.get("id"))
        else:
            peer_value = raw_config
        peer_id = _bare_channel_id(peer_value)
        key = alias.casefold()
        if key in seen:
            raise ValueError(f"duplicate private Telegram source alias: {alias}")
        seen.add(key)
        result.append(PrivateSource(alias=alias, peer_id=peer_id))
    return result


def configured_aliases() -> list[str]:
    try:
        return [item.alias for item in parse_sources()]
    except ValueError:
        return []


def _credentials() -> tuple[int, str, str]:
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session = os.getenv("TELEGRAM_USER_SESSION", "").strip()
    if not api_id_raw or not api_hash or not session:
        raise RuntimeError(
            "authenticated Telegram source is configured but TELEGRAM_API_ID, "
            "TELEGRAM_API_HASH or TELEGRAM_USER_SESSION is missing"
        )
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be numeric") from exc
    if api_id <= 0:
        raise RuntimeError("TELEGRAM_API_ID must be positive")
    return api_id, api_hash, session


def _message_limit() -> int:
    try:
        value = int(os.getenv("TELEGRAM_PRIVATE_SOURCE_LIMIT", str(DEFAULT_LIMIT)))
    except ValueError:
        value = DEFAULT_LIMIT
    return max(10, min(MAX_LIMIT, value))


def _extra_urls(message: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        url = str(value or "").strip()
        if not url or url.casefold() in seen:
            return
        seen.add(url.casefold())
        result.append(url)

    for entity in list(getattr(message, "entities", None) or []):
        add(getattr(entity, "url", ""))
    for row in list(getattr(message, "buttons", None) or []):
        for button in list(row or []):
            add(getattr(button, "url", ""))
    return result


def _private_message_url(entity: Any, message_id: int) -> str:
    username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
    if username:
        return f"https://telegram.me/{username}/{int(message_id)}"
    channel_id = _bare_channel_id(getattr(entity, "id", 0))
    return f"https://telegram.me/c/{channel_id}/{int(message_id)}"


def _source_alias(source: PrivateSource | str) -> str:
    if isinstance(source, PrivateSource):
        return source.alias
    return str(source or "").strip().lstrip("@")


def _to_monitor_message(
    monitor_module: Any,
    source: PrivateSource | str,
    entity: Any,
    message: Any,
):
    message_id = int(getattr(message, "id", 0) or 0)
    if message_id <= 0:
        return None
    alias = _source_alias(source)
    if not alias:
        return None
    raw_text = str(
        getattr(message, "raw_text", None)
        or getattr(message, "message", None)
        or ""
    ).strip()
    parts = [raw_text] if raw_text else []
    parts.extend(_extra_urls(message))
    text = "\n".join(dict.fromkeys(part for part in parts if part))
    date = getattr(message, "date", None) or monitor_module.now_utc()
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return monitor_module.Message(
        source=alias,
        message_id=message_id,
        date=date,
        text=text,
        message_url=_private_message_url(entity, message_id),
    )


def _resolve_entities(client: Any, sources: list[PrivateSource]) -> dict[str, Any]:
    wanted = {source.peer_id: source.alias for source in sources}
    found: dict[str, Any] = {}
    # Resolve through the authorized account's own dialogs. This neither joins
    # channels nor guesses invite links; only channels already visible to the
    # user's Telegram account can become sources.
    for dialog in client.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        try:
            entity_id = _bare_channel_id(getattr(entity, "id", 0))
        except ValueError:
            continue
        alias = wanted.get(entity_id)
        if alias:
            found[alias.casefold()] = entity
        if len(found) >= len(wanted):
            break
    return found


def _telethon_types():
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            f"dependency_missing: {type(exc).__name__}: {exc}"
        ) from exc
    return StringSession, TelegramClient


def fetch_private_sources(
    monitor_module: Any,
    requested: list[PrivateSource],
) -> tuple[dict[str, list[Any]], dict[str, str], list[str]]:
    results: dict[str, list[Any]] = {}
    errors: dict[str, str] = {}
    empty: list[str] = []
    if not requested:
        return results, errors, empty

    try:
        api_id, api_hash, session = _credentials()
        StringSession, TelegramClient = _telethon_types()
    except RuntimeError as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return {}, {source.alias: detail for source in requested}, []

    client = None
    try:
        client = TelegramClient(StringSession(session), api_id, api_hash)
        client.connect()
        if not client.is_user_authorized():
            detail = "authorization_required: Telegram user session is no longer authorized"
            return {}, {source.alias: detail for source in requested}, []
        entities = _resolve_entities(client, requested)
        limit = _message_limit()
        for source in requested:
            entity = entities.get(source.alias.casefold())
            if entity is None:
                errors[source.alias] = (
                    "private_source_not_found: configured channel is not present "
                    "in the authorized Telegram account dialogs"
                )
                continue
            try:
                rows = list(client.get_messages(entity, limit=limit) or [])
            except Exception as exc:
                errors[source.alias] = f"{type(exc).__name__}: {exc}"[:500]
                continue
            messages = []
            for row in rows:
                converted = _to_monitor_message(monitor_module, source, entity, row)
                if converted is not None:
                    messages.append(converted)
            messages.sort(key=lambda item: item.message_id)
            if messages:
                results[source.alias] = messages
            else:
                empty.append(source.alias)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        for source in requested:
            if source.alias not in results and source.alias not in errors:
                errors[source.alias] = detail
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
    return results, errors, empty


def fetch_authenticated_public_sources(
    monitor_module: Any,
    requested: list[str],
) -> tuple[dict[str, list[Any]], dict[str, str], list[str]]:
    """Read a small set of public aliases through the existing user session.

    This is a fallback only for public web feeds that opened successfully but
    yielded no Telegram messages. It never joins a channel and keeps the configured
    public alias as the source identity, so wheel deduplication remains unchanged.
    """

    aliases = [
        str(value or "").strip().lstrip("@")
        for value in requested
        if str(value or "").strip().lstrip("@")
    ]
    if not aliases:
        return {}, {}, []

    results: dict[str, list[Any]] = {}
    errors: dict[str, str] = {}
    empty: list[str] = []
    try:
        api_id, api_hash, session = _credentials()
        StringSession, TelegramClient = _telethon_types()
    except RuntimeError as exc:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        return {}, {alias: detail for alias in aliases}, []

    client = None
    try:
        client = TelegramClient(StringSession(session), api_id, api_hash)
        client.connect()
        if not client.is_user_authorized():
            detail = "authorization_required: Telegram user session is no longer authorized"
            return {}, {alias: detail for alias in aliases}, []
        limit = _message_limit()
        for alias in aliases:
            try:
                # Resolving a public @username does not join or subscribe the user.
                entity = client.get_entity(alias)
                rows = list(client.get_messages(entity, limit=limit) or [])
            except Exception as exc:
                errors[alias] = f"{type(exc).__name__}: {exc}"[:500]
                continue
            messages = []
            for row in rows:
                converted = _to_monitor_message(monitor_module, alias, entity, row)
                if converted is not None:
                    messages.append(converted)
            messages.sort(key=lambda item: item.message_id)
            if messages:
                results[alias] = messages
            else:
                empty.append(alias)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        for alias in aliases:
            if alias not in results and alias not in errors:
                errors[alias] = detail
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
    return results, errors, empty


def _authenticated_public_fallback_candidates(
    public_requested: list[str],
    empty: list[str],
) -> list[str]:
    """Bound fallback work so a correlated Telegram outage cannot fan out."""

    empty_keys = {str(value).casefold() for value in empty}
    if not empty_keys or len(empty_keys) > AUTH_PUBLIC_FALLBACK_MAX_SOURCES:
        return []
    return [
        source
        for source in public_requested
        if str(source).casefold() in empty_keys
    ]


def _apply_authenticated_public_fallback(
    results: dict[str, list[Any]],
    errors: dict[str, str],
    empty: list[str],
    fallback_results: dict[str, list[Any]],
) -> tuple[dict[str, list[Any]], dict[str, str], list[str]]:
    """Replace only empty public-feed outcomes that authenticated reads recovered."""

    recovered = {str(source).casefold() for source in fallback_results}
    if not recovered:
        return results, errors, empty
    for source, messages in fallback_results.items():
        results[source] = messages
        errors.pop(source, None)
        for key in list(errors):
            if str(key).casefold() == str(source).casefold():
                errors.pop(key, None)
    empty = [value for value in empty if str(value).casefold() not in recovered]
    return results, errors, empty


def _is_sources_path(monitor_module: Any, path: Any) -> bool:
    try:
        return Path(path).resolve() == Path(monitor_module.SOURCES_PATH).resolve()
    except (OSError, TypeError, ValueError):
        return False


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_private_telegram_sources_installed", False):
        return

    original_read_list: Callable = monitor_module.read_list
    original_fetch_all: Callable = monitor_module.fetch_all_sources

    def read_list_with_private_sources(path: Any):
        values = list(original_read_list(path))
        if not _is_sources_path(monitor_module, path):
            return values
        try:
            private = parse_sources()
        except ValueError as exc:
            print(f"WARNING private Telegram source config: {exc}")
            return values
        seen = {str(value).casefold() for value in values}
        for source in private:
            if source.alias.casefold() not in seen:
                values.append(source.alias)
                seen.add(source.alias.casefold())
        return values

    def fetch_all_with_private_sources(sources: list[str]):
        try:
            configured = parse_sources()
        except ValueError as exc:
            results, errors, empty = original_fetch_all(sources)
            return results, errors, empty

        by_alias = {source.alias.casefold(): source for source in configured}
        private_requested: list[PrivateSource] = []
        public_requested: list[str] = []
        for source in sources:
            private = by_alias.get(str(source).casefold())
            if private is None:
                public_requested.append(source)
            else:
                private_requested.append(private)

        results, errors, empty = original_fetch_all(public_requested)

        fallback_requested = _authenticated_public_fallback_candidates(
            public_requested,
            empty,
        )
        if fallback_requested:
            fallback_results, fallback_errors, fallback_empty = (
                fetch_authenticated_public_sources(
                    monitor_module,
                    fallback_requested,
                )
            )
            results, errors, empty = _apply_authenticated_public_fallback(
                results,
                errors,
                empty,
                fallback_results,
            )
            if fallback_errors:
                print(
                    "WARNING authenticated public Telegram fallback: "
                    + ", ".join(
                        f"{source}={detail}"
                        for source, detail in sorted(fallback_errors.items())
                    )[:1000]
                )
            if fallback_empty:
                print(
                    "INFO authenticated public Telegram fallback remained empty: "
                    + ", ".join(sorted(fallback_empty, key=str.casefold))
                )

        if private_requested:
            private_results, private_errors, private_empty = fetch_private_sources(
                monitor_module,
                private_requested,
            )
            results.update(private_results)
            errors.update(private_errors)
            empty.extend(private_empty)
        return results, errors, sorted(set(empty), key=str.casefold)

    # Existing production contracts intentionally expose the public collector as
    # telegram_transport. Keep that module identity stable while extending it.
    fetch_all_with_private_sources.__module__ = "telegram_transport"
    monitor_module.read_list = read_list_with_private_sources
    monitor_module.fetch_all_sources = fetch_all_with_private_sources
    monitor_module._bbvg_private_telegram_sources_installed = True


def self_test() -> None:
    parsed = parse_sources(
        json.dumps(
            {
                "private_wheels": -1001234567890,
                "second_private": {"peer_id": "987654321"},
            }
        )
    )
    assert [(row.alias, row.peer_id) for row in parsed] == [
        ("private_wheels", 1234567890),
        ("second_private", 987654321),
    ]
    assert _bare_channel_id(-1001234567890) == 1234567890
    assert _bare_channel_id(1234567890) == 1234567890

    class Entity:
        id = 1234567890
        username = None

    assert _private_message_url(Entity(), 77) == (
        "https://telegram.me/c/1234567890/77"
    )

    class Link:
        url = "https://betboom.ru/freestream/private-test"

    class Message:
        entities = [Link()]
        buttons = []

    assert _extra_urls(Message()) == [
        "https://betboom.ru/freestream/private-test"
    ]
    assert _authenticated_public_fallback_candidates(
        ["one", "bbwheel", "three"],
        ["bbwheel"],
    ) == ["bbwheel"]
    original_results = {"one": [1]}
    original_errors = {"bbwheel": "empty_public_feed"}
    merged_results, merged_errors, merged_empty = _apply_authenticated_public_fallback(
        original_results,
        original_errors,
        ["bbwheel"],
        {"bbwheel": [2, 3]},
    )
    assert merged_results == {"one": [1], "bbwheel": [2, 3]}
    assert "bbwheel" not in merged_errors
    assert merged_empty == []
    print("authenticated private/public Telegram source self-test passed")


if __name__ == "__main__":
    self_test()
