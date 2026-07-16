from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ACCESS_PATH = Path(__file__).resolve().parent / "bot_access.json"
UTC = timezone.utc
WHEEL_ID_RE = re.compile(r"Идентификатор:\s*<code>([^<]+)</code>", re.IGNORECASE)
WHEEL_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?betboom\.ru/freestream/([A-Za-z0-9._~-]+)",
    re.IGNORECASE,
)

USER_NOTIFICATION_MARKERS = (
    "новое колесо betboom",
    "колесо betboom стало активно",
    "колесо betboom подтверждено администратором",
    "колесо betboom доступно для участия",
    "участие откроется позже",
    "напоминание о колесе betboom",
    "время прокрутки колеса наступило",
    "активные колёса",
)

ADMIN_NOTIFICATION_MARKERS = (
    "ошибка",
    "сбой",
    "не смог проверить",
    "недоступ",
    "восстановлена",
    "служебн",
    "диагност",
)

USER_NOTIFICATION_KINDS = {"wheels", "daily_reports", "weekly_reports"}
ADMIN_NOTIFICATION_KINDS = {"admin_system", "admin_sources", "admin_requests"}
DELIVERY_DEDUP_SECONDS = max(
    300, int(os.getenv("NOTIFICATION_DEDUP_SECONDS", "86400"))
)
_delivered: dict[str, float] = {}
_delivery_lock = threading.RLock()
_pending_deliveries: set[str] = set()


def load_config() -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, False
    return (value if isinstance(value, dict) else {}), True


def notification_kind(text: str) -> str:
    lowered = html.unescape(str(text or "")).casefold()
    if "ежедневный отчёт" in lowered or "ежедневная сводка" in lowered:
        return "daily_reports"
    if "недельный отчёт" in lowered or "недельная сводка" in lowered:
        return "weekly_reports"
    if "запрос пользователя" in lowered and "источник" in lowered:
        return "admin_requests"
    source_failure = any(marker in lowered for marker in (
        "ошибок источников",
        "источник недоступен",
        "источников недоступна",
        "проблемы источников",
        "не смог проверить источник",
    ))
    if any(marker in lowered for marker in ADMIN_NOTIFICATION_MARKERS):
        return "admin_sources" if source_failure else "admin_system"
    if any(marker in lowered for marker in USER_NOTIFICATION_MARKERS):
        return "wheels"
    if source_failure:
        return "admin_sources"
    return "admin_system"


def classify(text: str) -> str:
    return "user" if notification_kind(text) in USER_NOTIFICATION_KINDS else "admin"


def user_notifications_enabled(config: dict[str, Any]) -> bool:
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    if "notifications" in settings:
        return bool(settings.get("notifications"))
    return bool(settings.get("wheel_notifications", True))


def user_for_chat(config: dict[str, Any], chat_id: str) -> tuple[str, dict[str, Any]]:
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    for user_id, record in users.items():
        if not isinstance(record, dict):
            continue
        if str(record.get("chat_id") or user_id) == str(chat_id):
            return str(user_id), record
    return "", {}


def admin_user_ids(config: dict[str, Any]) -> set[str]:
    return {
        value
        for value in {
            str(config.get("owner_id") or ""),
            *{str(item) for item in config.get("admins", [])},
        }
        if value
    }


def chat_for_user(config: dict[str, Any], user_id: str) -> str:
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    record = users.get(str(user_id))
    if isinstance(record, dict):
        return str(record.get("chat_id") or user_id)
    return str(user_id)


def is_admin_chat(config: dict[str, Any], chat_id: str) -> bool:
    user_id, _ = user_for_chat(config, chat_id)
    if user_id:
        return user_id in admin_user_ids(config)
    return str(chat_id) in {chat_for_user(config, user_id) for user_id in admin_user_ids(config)}


def participating_for_chat(
    config: dict[str, Any], chat_id: str, wheel_key: str
) -> bool:
    if not wheel_key:
        return False
    _, record = user_for_chat(config, chat_id)
    raw = record.get("participating_wheels") if isinstance(record, dict) else None
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, dict):
        values = raw.keys()
    else:
        return False
    return wheel_key.casefold() in {str(value).casefold() for value in values}


def preference_enabled(
    config: dict[str, Any], user_id: str, record: dict[str, Any], kind: str
) -> bool:
    admin = user_id in admin_user_ids(config)
    if kind in ADMIN_NOTIFICATION_KINDS and not admin:
        return False
    raw = record.get("notification_preferences")
    if isinstance(raw, dict) and kind in raw:
        return bool(raw[kind])
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    if kind == "wheels":
        enabled = record.get("notifications_enabled")
        if enabled is not None:
            return bool(enabled)
        legacy = {str(value) for value in config.get("notification_recipients", [])}
        chat_id = str(record.get("chat_id") or user_id)
        return chat_id in legacy if legacy else bool(settings.get("wheel_notifications", True))
    if kind in {"daily_reports", "weekly_reports"}:
        return False
    return admin


def recipients(config: dict[str, Any], config_exists: bool, category: str) -> list[str]:
    kind = {"admin": "admin_system", "user": "wheels"}.get(category, category)
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    if kind in ADMIN_NOTIFICATION_KINDS:
        result: set[str] = set()
        for user_id in admin_user_ids(config):
            raw = users.get(user_id)
            record = raw if isinstance(raw, dict) else {}
            if not preference_enabled(config, user_id, record, kind):
                continue
            chat_id = chat_for_user(config, user_id)
            if chat_id:
                result.add(chat_id)
        if result:
            return sorted(result)
    elif kind in USER_NOTIFICATION_KINDS:
        settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
        globally_enabled = (
            False
            if kind in {"daily_reports", "weekly_reports"}
            else user_notifications_enabled(config)
        )
        if not globally_enabled:
            return []
        legacy = {
            str(value)
            for value in config.get("notification_recipients", [])
            if str(value)
        }
        result: set[str] = set()
        for user_id, record in users.items():
            if not isinstance(record, dict):
                continue
            chat_id = str(record.get("chat_id") or user_id)
            if preference_enabled(config, str(user_id), record, kind):
                result.add(chat_id)
        if result:
            return sorted(result)
        if kind == "wheels" and not users and legacy:
            return sorted(legacy)

    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if fallback and not config_exists:
        return [fallback]
    return []


def delivery_key(chat_id: str, kind: str, text: str, url: str | None) -> str:
    raw = "\x1f".join([str(chat_id), str(kind), str(text), str(url or "")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def duplicate_delivery(key: str) -> bool:
    current = time.monotonic()
    with _delivery_lock:
        expired = [
            item
            for item, seen_at in _delivered.items()
            if current - seen_at > DELIVERY_DEDUP_SECONDS
        ]
        for item in expired:
            _delivered.pop(item, None)
        return key in _delivered


def remember_delivery(key: str) -> None:
    with _delivery_lock:
        _pending_deliveries.discard(key)
        _delivered[key] = time.monotonic()


def claim_delivery(key: str) -> bool:
    """Atomically reserve one delivery inside the single monitor process.

    The old ``duplicate_delivery`` followed by ``remember_delivery`` sequence
    left a race window: two worker threads could both see a missing key and send
    the same alert.  A claim is intentionally kept in memory until Telegram
    confirms the send.  Persistent deduplication is installed by
    ``notification_integrity_v2`` and retains the completed delivery.
    """

    with _delivery_lock:
        if key in _pending_deliveries or duplicate_delivery(key):
            return False
        _pending_deliveries.add(key)
        return True


def release_delivery(key: str) -> None:
    """Release a failed reservation so a later retry is not suppressed."""

    with _delivery_lock:
        _pending_deliveries.discard(key)


def complete_delivery(key: str) -> None:
    """Convert a successful reservation into a completed delivery mark."""

    remember_delivery(key)


def wheel_key_from_message(text: str, url: str | None, reply_markup: dict | None) -> str:
    match = WHEEL_ID_RE.search(text or "")
    if match:
        return html.unescape(match.group(1)).strip().casefold()
    for candidate in (url or "", text or ""):
        match = WHEEL_URL_RE.search(candidate)
        if match:
            return unquote(match.group(1)).strip().casefold()
    if isinstance(reply_markup, dict):
        for row in reply_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                callback = str(button.get("callback_data") or "")
                if callback.startswith(("bb:x:", "bb:t:", "wheel:inactive:", "wheel:time:")):
                    return callback.split(":", 2)[2].casefold()
                button_url = str(button.get("url") or "")
                match = WHEEL_URL_RE.search(button_url)
                if match:
                    return unquote(match.group(1)).strip().casefold()
    return ""


def notification_event_identity(
    kind: str,
    text: str,
    url: str | None,
    reply_markup: dict | None,
) -> str:
    """Return one stable delivery identity for one current wheel event.

    Telegram publications of the same wheel can contain different source names,
    countdown text or formatting.  Those differences are useful for statistics,
    but must not create a second user notification.  The notification kind keeps
    draw-time and final-reminder preferences independent from the initial alert.
    """

    wheel_key = wheel_key_from_message(text, url, reply_markup)
    if wheel_key and (kind == "wheels" or kind.startswith("wheel_")):
        lowered = html.unescape(str(text or "")).casefold()
        if "доступно для участия" in lowered or "теперь можно принять участие" in lowered:
            phase = "available"
        elif "откроется позже" in lowered or "будет доступно через" in lowered:
            phase = "scheduled"
        elif "последний шанс" in lowered or "напоминание о колесе" in lowered:
            phase = "reminder"
        elif "время прокрутки" in lowered and "наступило" in lowered:
            phase = "draw"
        elif "подтверждено администратором" in lowered:
            phase = "confirmed"
        elif "стало активно" in lowered:
            phase = "active"
        else:
            phase = "detected"
        event_token = ""
        if isinstance(reply_markup, dict):
            for row in reply_markup.get("inline_keyboard", []):
                if not isinstance(row, list):
                    continue
                for button in row:
                    if not isinstance(button, dict):
                        continue
                    callback = str(button.get("callback_data") or "")
                    if callback.startswith(("bb:p:", "bb:n:")):
                        event_token = callback.split(":", 2)[2].casefold()
                        break
                if event_token:
                    break
        suffix = f":{event_token}" if event_token else ""
        return f"wheel:{kind}:{wheel_key}:{phase}{suffix}"
    return ""


def hidden_for_chat(config: dict[str, Any], chat_id: str, wheel_key: str) -> bool:
    if not wheel_key:
        return False
    _, record = user_for_chat(config, chat_id)
    raw = record.get("hidden_wheels") if isinstance(record, dict) else None
    if isinstance(raw, list):
        return wheel_key in {str(value).casefold() for value in raw}
    if not isinstance(raw, dict):
        return False
    value = raw.get(wheel_key)
    if value is None:
        return False
    if not isinstance(value, dict):
        return True
    expires_raw = value.get("expires_at")
    if not isinstance(expires_raw, str) or not expires_raw:
        return True
    try:
        expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires.astimezone(UTC) > datetime.now(UTC)


def markup_for_chat(
    reply_markup: dict | None, *, admin: bool, participating: bool = False
) -> dict | None:
    if not isinstance(reply_markup, dict):
        return reply_markup
    result = copy.deepcopy(reply_markup)
    rows: list[list[dict[str, Any]]] = []
    for row in result.get("inline_keyboard", []):
        if not isinstance(row, list):
            continue
        filtered: list[dict[str, Any]] = []
        for button in row:
            if not isinstance(button, dict):
                continue
            value = dict(button)
            callback = str(value.get("callback_data") or "")
            if not admin and callback.startswith(("bb:t:", "wheel:time:")):
                continue
            if callback.startswith(("bb:p:", "wheel:part:")):
                if participating:
                    value["text"] = "✅ Участие отмечено"
                    if callback.startswith("bb:p:"):
                        value["callback_data"] = "bb:n:" + callback.split(":", 2)[2]
                else:
                    value["text"] = "✅ Участвую" if admin else "✅ Я участвую"
            if callback.startswith(("bb:x:", "wheel:inactive:")) and not admin:
                value["text"] = "Скрыть у меня"
            filtered.append(value)
        if filtered:
            rows.append(filtered)
    result["inline_keyboard"] = rows
    return result


def install(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_notification_router_installed", False):
        return

    def routed_send_message(
        text: str,
        url: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        config, exists = load_config()
        category = classify(text)
        kind = notification_kind(text)
        targets = recipients(config, exists, kind)
        if not targets:
            print(f"Notification has no recipients: {kind}")
            return {
                "ok": True,
                "result": {"suppressed": True, "category": category, "kind": kind},
            }

        key = wheel_key_from_message(text, url, reply_markup)
        event_identity = notification_event_identity(kind, text, url, reply_markup)
        result: dict[str, Any] = {"ok": True, "result": {"sent": 0}}
        errors: list[str] = []
        sent = 0
        skipped = 0
        for chat_id in targets:
            admin = is_admin_chat(config, chat_id)
            if category == "user" and hidden_for_chat(config, chat_id, key):
                skipped += 1
                continue
            dedup_key = delivery_key(
                chat_id,
                kind,
                event_identity or text,
                None if event_identity else url,
            )
            if not claim_delivery(dedup_key):
                skipped += 1
                continue
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            target_markup = markup_for_chat(
                reply_markup,
                admin=admin,
                participating=participating_for_chat(config, chat_id, key),
            )
            if target_markup is not None:
                payload["reply_markup"] = target_markup
            elif url:
                payload["reply_markup"] = {
                    "inline_keyboard": [[{"text": "Открыть колесо", "url": url}]]
                }
            try:
                response = monitor_module.telegram_api("sendMessage", payload)
                result = response
                sent += 1
            except Exception as exc:
                release_delivery(dedup_key)
                errors.append(f"{chat_id}:{type(exc).__name__}")
                print(f"WARNING notification target {chat_id}: {type(exc).__name__}: {exc}")
            else:
                complete_delivery(dedup_key)

        if errors and len(errors) == len(targets) - skipped:
            raise RuntimeError("All notification targets failed: " + ", ".join(errors))
        if not isinstance(result.get("result"), dict):
            result["result"] = {}
        result["result"]["sent"] = sent
        result["result"]["hidden_skipped"] = skipped
        result["result"]["category"] = category
        result["result"]["kind"] = kind
        return result

    monitor_module.send_message = routed_send_message
    monitor_module._bbvg_notification_router_installed = True


def self_test() -> None:
    config = {
        "owner_id": "1",
        "admins": ["2"],
        "notification_recipients": ["1", "3"],
        "settings": {
            "notifications": True,
            "daily_reports": True,
            "weekly_reports": True,
        },
        "users": {
            "1": {"chat_id": "1"},
            "2": {"chat_id": "2", "notifications_enabled": False},
            "3": {
                "chat_id": "3",
                "notifications_enabled": True,
                "notification_preferences": {"admin_system": True},
            },
        },
    }
    assert recipients(config, True, "admin") == ["1", "2"]
    assert recipients(config, True, "user") == ["1", "3"]
    assert recipients(config, True, "admin_system") == ["1", "2"]
    assert "3" not in recipients(config, True, "admin_system")
    assert classify("🎡 Новое колесо BetBoom") == "user"
    assert classify("✅ Колесо BetBoom подтверждено администратором") == "user"
    assert classify("⚠️ BB V.G. не смог проверить источник") == "admin"
    assert classify("⚠️ Ошибка в списке «Активные колёса»") == "admin"
    assert notification_kind("📊 Ежедневный отчёт BB V.G.") == "daily_reports"
    assert recipients(config, True, "daily_reports") == []
    assert recipients(config, True, "weekly_reports") == []
    assert notification_kind("📨 Запрос пользователя на добавление источника") == "admin_requests"
    key = delivery_key("1", "admin_system", "failure", None)
    assert not duplicate_delivery(key)
    remember_delivery(key)
    assert duplicate_delivery(key)
    first_event = notification_event_identity(
        "wheels",
        "🎡 Новое колесо BetBoom\nИдентификатор: <code>wheel-a</code>\n📡 @first",
        "https://betboom.ru/freestream/wheel-a",
        None,
    )
    second_event = notification_event_identity(
        "wheels",
        "🎡 Новое колесо BetBoom\nИдентификатор: <code>wheel-a</code>\n📡 @second",
        "https://betboom.ru/freestream/wheel-a?from=second",
        None,
    )
    assert first_event == second_event == "wheel:wheels:wheel-a:detected"
    assert notification_event_identity(
        "wheel_final_reminders",
        "Напоминание о колесе BetBoom\nИдентификатор: <code>wheel-a</code>",
        None,
        None,
    ) != first_event
    user_markup = markup_for_chat(
        {"inline_keyboard": [[{"text": "Время", "callback_data": "bb:t:test"}, {"text": "Неактивное", "callback_data": "bb:x:test"}]]},
        admin=False,
    )
    assert "bb:t:test" not in str(user_markup) and "Скрыть у меня" in str(user_markup)
    print("BB V.G. notification router self-test passed")


if __name__ == "__main__":
    self_test()
