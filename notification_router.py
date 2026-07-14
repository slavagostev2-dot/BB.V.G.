from __future__ import annotations

import copy
import html
import json
import os
import re
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
    "колесо betboom подтверждено администратором",
    "напоминание о колесе betboom",
    "время прокрутки колеса наступило",
    "активные колёса",
)


def load_config() -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, False
    return (value if isinstance(value, dict) else {}), True


def classify(text: str) -> str:
    lowered = html.unescape(str(text or "")).casefold()
    return "user" if any(marker in lowered for marker in USER_NOTIFICATION_MARKERS) else "admin"


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


def recipients(config: dict[str, Any], config_exists: bool, category: str) -> list[str]:
    if category == "admin":
        result = {
            chat_for_user(config, user_id)
            for user_id in admin_user_ids(config)
            if chat_for_user(config, user_id)
        }
        if result:
            return sorted(result)
    elif user_notifications_enabled(config):
        users = config.get("users") if isinstance(config.get("users"), dict) else {}
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
            enabled = record.get("notifications_enabled")
            if enabled is None:
                enabled = not legacy or chat_id in legacy
            if bool(enabled):
                result.add(chat_id)
        if result:
            return sorted(result)
        if not users and legacy:
            return sorted(legacy)

    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if fallback and not config_exists:
        return [fallback]
    return []


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


def markup_for_chat(reply_markup: dict | None, *, admin: bool) -> dict | None:
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
        targets = recipients(config, exists, category)
        if not targets:
            print(f"Notification has no recipients: {category}")
            return {"ok": True, "result": {"suppressed": True, "category": category}}

        key = wheel_key_from_message(text, url, reply_markup)
        result: dict[str, Any] = {"ok": True, "result": {"sent": 0}}
        errors: list[str] = []
        sent = 0
        skipped = 0
        for chat_id in targets:
            admin = is_admin_chat(config, chat_id)
            if category == "user" and hidden_for_chat(config, chat_id, key):
                skipped += 1
                continue
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            target_markup = markup_for_chat(reply_markup, admin=admin)
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
                errors.append(f"{chat_id}:{type(exc).__name__}")
                print(f"WARNING notification target {chat_id}: {type(exc).__name__}: {exc}")

        if errors and len(errors) == len(targets) - skipped:
            raise RuntimeError("All notification targets failed: " + ", ".join(errors))
        if not isinstance(result.get("result"), dict):
            result["result"] = {}
        result["result"]["sent"] = sent
        result["result"]["hidden_skipped"] = skipped
        result["result"]["category"] = category
        return result

    monitor_module.send_message = routed_send_message
    monitor_module._bbvg_notification_router_installed = True


def self_test() -> None:
    config = {
        "owner_id": "1",
        "admins": ["2"],
        "notification_recipients": ["1", "3"],
        "settings": {"wheel_notifications": True},
        "users": {
            "1": {"chat_id": "1"},
            "2": {"chat_id": "2", "notifications_enabled": False},
            "3": {"chat_id": "3", "notifications_enabled": True},
        },
    }
    assert recipients(config, True, "admin") == ["1", "2"]
    assert recipients(config, True, "user") == ["1", "3"]
    assert classify("🎡 Новое колесо BetBoom") == "admin"
    assert classify("✅ Колесо BetBoom подтверждено администратором") == "user"
    assert classify("⚠️ BB V.G. не смог проверить источник") == "admin"
    user_markup = markup_for_chat(
        {"inline_keyboard": [[{"text": "Время", "callback_data": "bb:t:test"}, {"text": "Неактивное", "callback_data": "bb:x:test"}]]},
        admin=False,
    )
    assert "bb:t:test" not in str(user_markup) and "Скрыть у меня" in str(user_markup)
    print("BB V.G. notification router self-test passed")


if __name__ == "__main__":
    self_test()
