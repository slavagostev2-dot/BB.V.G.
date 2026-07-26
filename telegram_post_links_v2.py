from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


MINIMUM_FRESH_UNKNOWN_MINUTES = 360
POST_MARKER_RE = re.compile(r'data-post="([^"/]+)/(\d+)"', re.IGNORECASE)
WHEEL_CONTEXT_RE = re.compile(
    r"\b(?:колес\w*|крутил\w*|прокрут\w*|wheel\w*|spin\w*)\b",
    re.IGNORECASE,
)
BETBOOM_CONTEXT_RE = re.compile(
    r"\b(?:betboom|bet\s*boom|бетбум|бэтбум)\b", re.IGNORECASE
)
ANNOUNCEMENT_ACTION_RE = re.compile(
    r"\b(?:сегодня|завтра|скоро|сейчас|начал\w*|старт\w*|крутим\w*|"
    r"прокрут\w*|розыгрыш\w*|участв\w*|ссылк\w*|позже)\b",
    re.IGNORECASE,
)
CURRENT_ACTION_RE = re.compile(
    r"\b(?:сейчас|уже|начал\w*|ид[её]т|крутим\w*|стартовал\w*)\b",
    re.IGNORECASE,
)
PARTICIPATION_EVIDENCE_RE = re.compile(
    r"\b(?:участв\w*|ссылк\w*|розыгрыш\w*|приз\w*)\b",
    re.IGNORECASE,
)
COLLECTOR_CURSOR_STATE_KEY = "telegram_collector_cursors"
COLLECTOR_DIRECT_PROBE_LIMIT = max(
    3, int(os.getenv("COLLECTOR_DIRECT_PROBE_LIMIT", "8"))
)
COLLECTOR_GAP_SCAN_LIMIT = max(
    COLLECTOR_DIRECT_PROBE_LIMIT,
    int(os.getenv("COLLECTOR_GAP_SCAN_LIMIT", "80")),
)

_ACTIVE_MONITOR_STATE: dict[str, Any] | None = None
_COLLECTOR_CURSOR_DIRTY = False


def _post_segments(page: str):
    """Yield one raw HTML segment per Telegram post."""

    matches = list(POST_MARKER_RE.finditer(page or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(page)
        yield match.group(1), int(match.group(2)), page[match.start():end]


def parse_public_channel_html(monitor_module: Any, username: str, page: str):
    """Parse post text and URL buttons from the complete per-post segment."""

    result = []
    for source, message_id, segment in _post_segments(page or ""):
        observed_source = source or username
        # A public username may redirect to another configured channel. Alias
        # registration is process-global, so registering that redirect would
        # corrupt both collectors. Preserve the request namespace locally.
        canonical_source = str(username or observed_source).strip().lstrip("@")
        fragment = BeautifulSoup(segment, "html.parser")
        parts: list[str] = []

        text_node = fragment.select_one("div.tgme_widget_message_text")
        if text_node is not None:
            parts.append(text_node.get_text("\n", strip=True))

        for anchor in fragment.select("a[href]"):
            href = html.unescape(str(anchor.get("href") or "")).strip()
            if href:
                parts.append(href)

        for raw_href in re.findall(
            r'href=["\']([^"\']+)["\']', segment, re.IGNORECASE
        ):
            href = html.unescape(raw_href).strip()
            if href:
                parts.append(href)

        time_node = fragment.select_one("time[datetime]")
        date_text = str(time_node.get("datetime") or "") if time_node else ""
        if not date_text:
            match = re.search(
                r'<time[^>]+datetime=["\']([^"\']+)', segment, re.IGNORECASE
            )
            date_text = match.group(1) if match else ""
        try:
            date = (
                datetime.fromisoformat(date_text)
                if date_text
                else monitor_module.now_utc()
            )
        except ValueError:
            date = monitor_module.now_utc()
        if date.tzinfo is None:
            date = date.replace(tzinfo=monitor_module.UTC)

        result.append(
            monitor_module.Message(
                source=canonical_source,
                message_id=message_id,
                date=date,
                text=monitor_module.telegram_transport.rewrite_telegram_text(
                    "\n".join(dict.fromkeys(part for part in parts if part))
                ),
                message_url=monitor_module.telegram_transport.public_message_url(
                    observed_source, message_id
                ),
            )
        )
    return sorted(result, key=lambda item: item.message_id)


def fresh_public_source_url(
    monitor_module: Any,
    username: str,
    *,
    before: int | None = None,
) -> str:
    """Use a short-lived query token so Telegram/CDN cannot reuse a stale preview."""

    base = monitor_module.telegram_transport.public_source_url(
        username, before=before
    )
    slot = int(monitor_module.now_utc().timestamp() // 30)
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}bbvg_fresh={slot}"



def _collector_sources(monitor_module: Any) -> set[str]:
    try:
        payload = json.loads(
            monitor_module.IDENTIFIER_SOURCES_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        payload = {}
    return {
        str(value or "").strip().lstrip("@").casefold()
        for value in (payload.get("collectors", []) if isinstance(payload, dict) else [])
        if str(value or "").strip().lstrip("@")
    }


def _message_identity(message: Any) -> tuple[str, int]:
    source = str(getattr(message, "source", "") or "").strip().lstrip("@").casefold()
    try:
        message_id = int(getattr(message, "message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    return source, message_id


def _message_url_source(message: Any) -> str:
    try:
        path = urlsplit(str(getattr(message, "message_url", "") or "")).path
    except ValueError:
        return ""
    parts = [part for part in path.split("/") if part]
    return parts[0].casefold() if len(parts) >= 2 else ""


def _known_direct_message_ids(state: dict[str, Any], source: str) -> set[int]:
    """Return only IDs evidenced by a URL in the configured source namespace."""

    normalized = str(source or "").strip().lstrip("@").casefold()
    result: set[int] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            raw_source = str(value.get("source") or "").strip().lstrip("@").casefold()
            url_source = ""
            try:
                path = urlsplit(str(value.get("message_url") or "")).path
            except ValueError:
                path = ""
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2:
                url_source = parts[0].casefold()
            if raw_source == normalized or url_source == normalized:
                try:
                    message_id = int(value.get("message_id") or 0)
                except (TypeError, ValueError):
                    message_id = 0
                if message_id > 0 and (not url_source or url_source == normalized):
                    result.add(message_id)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    for key in (
        "wheel_publications",
        "active_wheels",
        "pending_posts",
        "wheel_generation_observations",
    ):
        collect(state.get(key))
    return result


def _merge_messages(*pages: list[Any]) -> list[Any]:
    merged: dict[tuple[str, int], Any] = {}
    for page in pages:
        for message in page:
            identity = _message_identity(message)
            if identity[1] > 0:
                merged[identity] = message
    return sorted(
        merged.values(),
        key=lambda message: (
            getattr(message, "date", None),
            _message_identity(message)[0],
            _message_identity(message)[1],
        ),
    )


def fetch_direct_public_post(
    monitor_module: Any,
    source: str,
    message_id: int,
):
    """Fetch one immutable Telegram post independently from the moving preview."""

    base = monitor_module.telegram_transport.public_message_url(source, message_id)
    slot = int(monitor_module.now_utc().timestamp() // 30)
    response = monitor_module.request_with_retries(
        "GET",
        f"{base}?embed=1&mode=tme&bbvg_fresh={slot}-{message_id}",
        attempts=1,
        timeout=min(8, int(monitor_module.REQUEST_TIMEOUT)),
        headers={
            "User-Agent": monitor_module.USER_AGENT,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
        allow_redirects=True,
    )
    response.raise_for_status()
    parsed = parse_public_channel_html(monitor_module, source, response.text)
    for message in parsed:
        if _message_identity(message)[1] == int(message_id):
            return message
    # A Telegram username redirect may rewrite both data-post source and ID.
    # The immutable URL requested above is authoritative. Embed pages contain
    # one post; normalize that post back to the configured public namespace so
    # a redirect can never poison the configured source cursor.
    if len(parsed) == 1:
        message = parsed[0]
        return monitor_module.Message(
            source=str(source).strip().lstrip("@"),
            message_id=int(message_id),
            date=message.date,
            text=message.text,
            message_url=monitor_module.telegram_transport.public_message_url(
                source,
                message_id,
            ),
        )
    return None


def recover_collector_message_gaps(
    monitor_module: Any,
    state: dict[str, Any],
    results: dict[str, list[Any]],
    errors: dict[str, str],
    empty: list[str],
    sources: list[str],
    *,
    collector_sources: set[str] | None = None,
    direct_fetcher: Callable[[str, int], Any | None] | None = None,
) -> dict[str, Any]:
    """Recover collector posts by monotonic Telegram message ID.

    Telegram's public ``/s/channel`` preview is a moving, edge-dependent window.
    A non-empty HTTP 200 response does not prove that its highest message ID is
    current. Collector channels are therefore reconciled against a persisted
    cursor and the immutable direct embed endpoint before classification.
    """

    configured = collector_sources if collector_sources is not None else _collector_sources(
        monitor_module
    )
    configured = {str(value).casefold() for value in configured}
    cursor_rows = state.setdefault(COLLECTOR_CURSOR_STATE_KEY, {})
    summary: dict[str, Any] = {
        "checked_collectors": 0,
        "recovered_messages": 0,
        "recovered_sources": {},
        "listing_failures_recovered": 0,
        "namespace_resets": {},
        "changed": False,
    }
    fetch_one = direct_fetcher or (
        lambda source, message_id: fetch_direct_public_post(
            monitor_module, source, message_id
        )
    )

    for source in sources:
        normalized = str(source or "").strip().lstrip("@").casefold()
        if normalized not in configured:
            continue
        summary["checked_collectors"] = int(summary["checked_collectors"]) + 1
        listed = list(results.get(source, []))
        listed_ids = {
            _message_identity(message)[1]
            for message in listed
            if _message_identity(message)[1] > 0
        }
        direct_namespace_ids = {
            _message_identity(message)[1]
            for message in listed
            if _message_identity(message)[1] > 0
            and _message_url_source(message) in {"", normalized}
        }
        page_max = max(direct_namespace_ids, default=0)
        record = cursor_rows.get(normalized)
        record = dict(record) if isinstance(record, dict) else {}
        try:
            stored_cursor = int(record.get("last_message_id", 0) or 0)
        except (TypeError, ValueError):
            stored_cursor = 0
        known_direct_ids = _known_direct_message_ids(state, normalized)
        known_direct_max = max(known_direct_ids, default=0)
        redirected_listing = bool(listed_ids and not direct_namespace_ids)
        namespace_reset = False

        def reset_namespace_cursor(reason: str) -> None:
            nonlocal stored_cursor, namespace_reset
            record["namespace_reset_from"] = stored_cursor
            record["namespace_reset_to"] = known_direct_max
            record["namespace_reset_at"] = monitor_module.now_utc().isoformat()
            record["namespace_reset_reason"] = reason
            stored_cursor = known_direct_max
            namespace_reset = True
            summary["namespace_resets"][source] = {
                "from": record["namespace_reset_from"],
                "to": known_direct_max,
                "reason": reason,
            }

        if (
            redirected_listing
            and known_direct_max > 0
            and stored_cursor > known_direct_max
        ):
            reset_namespace_cursor("redirected_listing_id_mismatch")
        elif known_direct_max > 0 and stored_cursor > known_direct_max:
            # A collector listing may time out while its persisted cursor still
            # belongs to a Telegram username that the collector once redirected
            # to. Validate that suspicious cursor through the immutable post URL
            # before using it as the base for future probes. Without this check a
            # poisoned cursor such as kolesaBB=71862 makes the recovery probe an
            # unrelated namespace forever and skips real posts 250, 251, ...
            validation_completed = False
            try:
                stored_message = fetch_one(source, stored_cursor)
                validation_completed = True
            except Exception as exc:
                stored_message = None
                print(
                    "WARNING collector stored cursor validation failed: "
                    f"@{source}/{stored_cursor} {type(exc).__name__}: {exc}"
                )
            stored_identity = (
                _message_identity(stored_message)
                if stored_message is not None
                else ("", 0)
            )
            stored_source = (
                _message_url_source(stored_message)
                if stored_message is not None
                else ""
            )
            if validation_completed and (
                stored_identity[1] != stored_cursor
                or stored_source not in {"", normalized}
            ):
                reset_namespace_cursor("invalid_direct_cursor")

        probe_ids: list[int] = []
        if stored_cursor > 0 and page_max > stored_cursor:
            for candidate in range(stored_cursor + 1, page_max + 1):
                if candidate not in listed_ids:
                    probe_ids.append(candidate)
                    if len(probe_ids) >= COLLECTOR_GAP_SCAN_LIMIT:
                        break
        future_base = max(stored_cursor, page_max)
        if future_base > 0:
            future_probe_limit = (
                COLLECTOR_GAP_SCAN_LIMIT
                if namespace_reset
                else COLLECTOR_DIRECT_PROBE_LIMIT
            )
            probe_ids.extend(
                range(
                    future_base + 1,
                    future_base + future_probe_limit + 1,
                )
            )
        probe_ids = list(dict.fromkeys(probe_ids))

        recovered: list[Any] = []
        consecutive_missing = 0
        for candidate in probe_ids:
            try:
                message = fetch_one(source, candidate)
            except Exception as exc:
                print(
                    "WARNING collector direct cursor probe failed: "
                    f"@{source}/{candidate} {type(exc).__name__}: {exc}"
                )
                break
            if message is not None:
                recovered.append(message)
                consecutive_missing = 0
            elif namespace_reset and candidate > future_base:
                consecutive_missing += 1
                if consecutive_missing >= 3:
                    break

        authoritative_listed = [
            message
            for message in listed
            if _message_url_source(message) in {"", normalized}
        ]
        merged = _merge_messages(authoritative_listed, recovered)
        merged_ids = {
            _message_identity(message)[1]
            for message in merged
            if _message_identity(message)[1] > 0
            and _message_url_source(message) in {"", normalized}
        }
        if merged:
            results[source] = merged
            if source in empty:
                empty.remove(source)
        if recovered:
            if source in errors:
                errors.pop(source, None)
                summary["listing_failures_recovered"] = int(
                    summary["listing_failures_recovered"]
                ) + 1
            recovered_ids = sorted(
                {
                    _message_identity(message)[1]
                    for message in recovered
                    if _message_identity(message)[1] > 0
                }
            )
            summary["recovered_messages"] = int(summary["recovered_messages"]) + len(
                recovered_ids
            )
            summary["recovered_sources"][source] = recovered_ids
            print(
                "Recovered Telegram collector gap: "
                f"@{source} message_ids={','.join(str(value) for value in recovered_ids)}"
            )
            record["last_direct_recovered_at"] = monitor_module.now_utc().isoformat()
            record["last_direct_recovered_ids"] = recovered_ids
            record["direct_recovered_total"] = int(
                record.get("direct_recovered_total", 0) or 0
            ) + len(recovered_ids)

        highest = max([stored_cursor, *merged_ids], default=stored_cursor)
        before = dict(record)
        if highest > 0:
            record["last_message_id"] = highest
        if page_max > 0:
            record["last_page_message_id"] = page_max
        if page_max and stored_cursor and page_max < stored_cursor:
            record["last_stale_listing_at"] = monitor_module.now_utc().isoformat()
        if redirected_listing:
            record["last_redirected_listing_at"] = monitor_module.now_utc().isoformat()
        if recovered:
            record["last_gap_recovered_from"] = min(
                _message_identity(message)[1] for message in recovered
            )
            record["last_gap_recovered_to"] = max(
                _message_identity(message)[1] for message in recovered
            )
        if record != before or normalized not in cursor_rows:
            cursor_rows[normalized] = record
            summary["changed"] = True
        try:
            store_factory = getattr(monitor_module, "event_store", None)
            if callable(store_factory):
                store_factory().update_source_cursor(
                    normalized,
                    listing_message_id=page_max,
                    direct_message_id=highest,
                    stale_listing=bool(
                        redirected_listing
                        or (page_max and stored_cursor and page_max < stored_cursor)
                    ),
                    recovered_count=len(recovered),
                )
        except Exception as exc:
            print(
                "WARNING durable source cursor checkpoint failed: "
                f"@{source} {type(exc).__name__}: {exc}"
            )

    state["last_collector_cursor_recovery"] = {
        "checked_collectors": summary["checked_collectors"],
        "recovered_messages": summary["recovered_messages"],
        "recovered_sources": summary["recovered_sources"],
        "listing_failures_recovered": summary["listing_failures_recovered"],
        "namespace_resets": summary["namespace_resets"],
    }
    return summary


def _install_collector_cursor_recovery(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_collector_cursor_recovery_installed", False):
        return
    original_load_state: Callable = monitor_module.load_state
    original_fetch_all: Callable = monitor_module.fetch_all_sources
    original_save_health: Callable = monitor_module.data_store.save_health

    def load_state_with_collector_cursor():
        global _ACTIVE_MONITOR_STATE
        state = original_load_state()
        if isinstance(state, dict):
            state.setdefault(COLLECTOR_CURSOR_STATE_KEY, {})
            _ACTIVE_MONITOR_STATE = state
        return state

    def fetch_all_with_collector_cursor(sources: list[str]):
        global _COLLECTOR_CURSOR_DIRTY
        results, errors, empty = original_fetch_all(sources)
        state = _ACTIVE_MONITOR_STATE
        if isinstance(state, dict):
            summary = recover_collector_message_gaps(
                monitor_module,
                state,
                results,
                errors,
                empty,
                sources,
            )
            _COLLECTOR_CURSOR_DIRTY = bool(summary.get("changed")) or _COLLECTOR_CURSOR_DIRTY
        return results, errors, empty

    def save_health_with_collector_cursor(value: dict[str, Any]) -> None:
        global _COLLECTOR_CURSOR_DIRTY
        if _COLLECTOR_CURSOR_DIRTY and isinstance(_ACTIVE_MONITOR_STATE, dict):
            monitor_module.save_state(_ACTIVE_MONITOR_STATE)
            _COLLECTOR_CURSOR_DIRTY = False
        original_save_health(value)

    fetch_all_with_collector_cursor.__module__ = "telegram_transport"
    monitor_module.load_state = load_state_with_collector_cursor
    monitor_module.fetch_all_sources = fetch_all_with_collector_cursor
    monitor_module.data_store.save_health = save_health_with_collector_cursor
    monitor_module._bbvg_collector_cursor_recovery_installed = True


def _ai_wheel_evidence_cap(text: str, classification: str = "") -> float:
    """Cap AI confidence by explicit, independently verifiable post evidence."""

    value = str(text or "")
    if not WHEEL_CONTEXT_RE.search(value) or not ANNOUNCEMENT_ACTION_RE.search(value):
        return 0.0

    has_brand = bool(BETBOOM_CONTEXT_RE.search(value))
    has_participation = bool(PARTICIPATION_EVIDENCE_RE.search(value))
    has_current_action = bool(CURRENT_ACTION_RE.search(value))
    category = str(classification or "").casefold()

    if not has_brand:
        return 0.79 if has_participation else 0.49
    if category == "active_wheel" and not has_current_action:
        return 0.69
    if has_current_action and has_participation:
        return 0.96
    if has_participation:
        return 0.93
    return 0.90


def _install_suspicious_post_policy(suspicious_posts: Any) -> None:
    """Install strict evidence handling only around monitor delivery."""

    if getattr(suspicious_posts, "_bbvg_strict_evidence_policy_installed", False):
        return
    os.environ.setdefault("AI_SUSPICIOUS_POST_MIN_CONFIDENCE", "0.90")
    os.environ.setdefault("AI_SUSPICIOUS_ACTIVE_MIN_CONFIDENCE", "0.93")
    original_run_for_messages = suspicious_posts.run_for_messages

    def run_for_messages_with_evidence(
        monitor_module: Any,
        messages_by_source: dict[str, list[Any]],
    ) -> dict[str, Any]:
        filtered: dict[str, list[Any]] = {}
        for source, messages in messages_by_source.items():
            filtered[source] = [
                message
                for message in messages
                if _ai_wheel_evidence_cap(
                    str(getattr(message, "text", "") or ""),
                    "possible_wheel_announcement",
                )
                >= 0.90
            ]

        original_analyze_posts = suspicious_posts.analyze_posts

        def analyze_posts_with_evidence(
            posts: Any,
            state: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            post_rows = list(posts)
            summary = original_analyze_posts(post_rows, state, **kwargs)
            original_alerts = list(summary.get("alerts", []))
            by_key = {suspicious_posts._key(post): post for post in post_rows}
            records = (
                state.get("posts") if isinstance(state.get("posts"), dict) else {}
            )
            base_threshold = suspicious_posts._float_env(
                "AI_SUSPICIOUS_POST_MIN_CONFIDENCE", 0.90, 0.50, 0.99
            )
            active_threshold = max(
                base_threshold,
                suspicious_posts._float_env(
                    "AI_SUSPICIOUS_ACTIVE_MIN_CONFIDENCE", 0.93, 0.50, 0.99
                ),
            )
            kept: list[dict[str, Any]] = []

            for alert in original_alerts:
                record_key = str(alert.get("record_key") or "")
                post = by_key.get(record_key)
                if post is None:
                    continue
                classification = str(alert.get("classification") or "uncertain")
                cap = _ai_wheel_evidence_cap(post.text, classification)
                confidence = min(float(alert.get("confidence", 0.0) or 0.0), cap)
                required = (
                    active_threshold
                    if classification == "active_wheel"
                    else base_threshold
                )
                row = records.get(record_key) if isinstance(records, dict) else None
                if isinstance(row, dict):
                    row["confidence"] = confidence
                    row["evidence_confidence_cap"] = cap
                    row["evidence_policy"] = "explicit_betboom_action_v1"
                alert["confidence"] = confidence
                if confidence >= required:
                    kept.append(alert)

            summary["alerts"] = kept
            summary["alerts_suppressed_by_evidence"] = max(
                0, len(original_alerts) - len(kept)
            )
            return summary

        suspicious_posts.analyze_posts = analyze_posts_with_evidence
        try:
            return original_run_for_messages(monitor_module, filtered)
        finally:
            suspicious_posts.analyze_posts = original_analyze_posts

    suspicious_posts.run_for_messages = run_for_messages_with_evidence
    suspicious_posts._bbvg_strict_evidence_policy_installed = True


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_telegram_button_links_installed", False):
        return

    def fetch_public_channel_with_buttons(
        username: str,
        before: int | None = None,
    ):
        response = monitor_module.request_with_retries(
            "GET",
            fresh_public_source_url(
                monitor_module,
                username,
                before=before,
            ),
            timeout=monitor_module.REQUEST_TIMEOUT,
            headers={
                "User-Agent": monitor_module.USER_AGENT,
                "Cache-Control": "no-cache, no-store, max-age=0",
                "Pragma": "no-cache",
            },
            allow_redirects=True,
        )
        response.raise_for_status()
        return parse_public_channel_html(monitor_module, username, response.text)

    monitor_module.fetch_public_channel = fetch_public_channel_with_buttons
    monitor_module.FRESH_UNKNOWN_POST_MINUTES = max(
        int(getattr(monitor_module, "FRESH_UNKNOWN_POST_MINUTES", 0) or 0),
        MINIMUM_FRESH_UNKNOWN_MINUTES,
    )
    monitor_module._bbvg_telegram_button_links_installed = True

    try:
        from bbvg.monitor import suspicious_posts

        _install_suspicious_post_policy(suspicious_posts)
        suspicious_posts.install(monitor_module)
        monitor_module.fetch_all_sources.__module__ = "telegram_transport"
    except Exception as exc:
        print(
            "WARNING suspicious-post analysis integration failed: "
            f"{type(exc).__name__}: {exc}"
        )

    try:
        import wheel_detection_reliability

        runtime_loaded = "bbvg_monitor_runtime" in sys.modules
        wheel_detection_reliability.install(monitor_module)
        if runtime_loaded and not getattr(
            monitor_module,
            "_bbvg_recovered_notification_guard_installed",
            False,
        ):
            raise RuntimeError("Recovered notification delivery guard was not installed")
    except Exception as exc:
        print(
            "WARNING wheel detection reliability integration failed: "
            f"{type(exc).__name__}: {exc}"
        )

    _install_collector_cursor_recovery(monitor_module)


def self_test() -> None:
    import monitor

    page = """
    <div class="tgme_widget_message_wrap">
      <div class="tgme_widget_message" data-post="jestercast/1516">
        <div class="tgme_widget_message_text">Новое колесо</div>
        <time datetime="2026-07-14T10:58:17+00:00"></time>
      </div>
    </div>
    <div class="tgme_widget_message_inline_buttons">
      <a href="https://betboom.ru/freestream/cct1">Участвовать</a>
    </div>
    <div class="tgme_widget_message" data-post="jestercast/1517">
      <div class="tgme_widget_message_text">Следующий пост</div>
      <time datetime="2026-07-14T11:00:00+00:00"></time>
    </div>
    """
    messages = parse_public_channel_html(monitor, "jestercast", page)
    assert len(messages) == 2
    assert messages[0].message_id == 1516
    assert monitor.extract_links(messages[0].text) == [
        "https://betboom.ru/freestream/cct1"
    ]
    assert monitor.extract_links(messages[1].text) == []
    assert _ai_wheel_evidence_cap("Колесо будет на стриме", "active_wheel") < 0.50
    assert _ai_wheel_evidence_cap(
        "BetBoom: сейчас крутим колесо, участвуйте в розыгрыше",
        "active_wheel",
    ) >= 0.93
    fresh = fresh_public_source_url(monitor, "jestercast")
    assert "bbvg_fresh=" in fresh
    before = fresh_public_source_url(monitor, "jestercast", before=100)
    assert "before=100" in before and "&bbvg_fresh=" in before

    listed = [
        monitor.Message(
            source="kolesaBB",
            message_id=249,
            date=datetime.fromisoformat("2026-07-25T11:38:08+00:00"),
            text="https://betboom.ru/freestream/CTOM19",
            message_url="https://telegram.me/kolesaBB/249",
        )
    ]
    recovered_rows = {
        250: monitor.Message(
            source="kolesaBB",
            message_id=250,
            date=datetime.fromisoformat("2026-07-25T13:39:30+00:00"),
            text="https://betboom.ru/freestream/pomidor1",
            message_url="https://telegram.me/kolesaBB/250",
        ),
        251: monitor.Message(
            source="kolesaBB",
            message_id=251,
            date=datetime.fromisoformat("2026-07-25T13:48:17+00:00"),
            text="https://betboom.ru/freestream/CTOM22",
            message_url="https://telegram.me/kolesaBB/251",
        ),
    }
    cursor_state: dict[str, Any] = {}
    cursor_results = {"kolesaBB": listed}
    cursor_errors: dict[str, str] = {}
    cursor_empty: list[str] = []
    summary = recover_collector_message_gaps(
        monitor,
        cursor_state,
        cursor_results,
        cursor_errors,
        cursor_empty,
        ["kolesaBB"],
        collector_sources={"kolesabb"},
        direct_fetcher=lambda _source, message_id: recovered_rows.get(message_id),
    )
    assert [message.message_id for message in cursor_results["kolesaBB"]] == [
        249,
        250,
        251,
    ]
    assert summary["recovered_messages"] == 2
    assert cursor_state[COLLECTOR_CURSOR_STATE_KEY]["kolesabb"]["last_message_id"] == 251

    error_results: dict[str, list[Any]] = {}
    error_rows = {252: monitor.Message(
        source="kolesaBB",
        message_id=252,
        date=datetime.fromisoformat("2026-07-25T14:00:00+00:00"),
        text="обычный новый пост",
        message_url="https://telegram.me/kolesaBB/252",
    )}
    error_summary = recover_collector_message_gaps(
        monitor,
        cursor_state,
        error_results,
        {"kolesaBB": "simulated stale listing failure"},
        [],
        ["kolesaBB"],
        collector_sources={"kolesabb"},
        direct_fetcher=lambda _source, message_id: error_rows.get(message_id),
    )
    assert [message.message_id for message in error_results["kolesaBB"]] == [252]
    assert error_summary["listing_failures_recovered"] == 1
    assert cursor_state[COLLECTOR_CURSOR_STATE_KEY]["kolesabb"]["last_message_id"] == 252

    install(monitor)
    assert monitor.FRESH_UNKNOWN_POST_MINUTES >= 360
    assert monitor._bbvg_ai_suspicious_post_analysis_installed is True
    assert monitor.fetch_all_sources.__module__ == "telegram_transport"
    print("telegram post parser, freshness and strict AI evidence self-test passed")


if __name__ == "__main__":
    self_test()
