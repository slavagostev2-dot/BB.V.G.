from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ACCESS_PATH = Path(__file__).resolve().parent / "bot_access.json"

DEFAULT_SETTINGS = {
    "wheel_notifications": True,
    "service_notifications": True,
    "daily_reports": True,
    "weekly_reports": True,
}


def load_config() -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, False
    return (value if isinstance(value, dict) else {}), True


def classify(text: str) -> str:
    lowered = text.casefold()
    if "ежедневный отчёт" in lowered:
        return "daily_reports"
    if "колёса не обнаружены" in lowered or "без колёс" in lowered and text.lstrip().startswith("📭"):
        return "weekly_reports"
    if text.lstrip().startswith(("🤖", "⚠️", "✅ <b>Ручная проверка", "🩺")):
        return "service_notifications"
    return "wheel_notifications"


def enabled_for(config: dict[str, Any], category: str) -> bool:
    settings = dict(DEFAULT_SETTINGS)
    raw = config.get("settings")
    if isinstance(raw, dict):
        for key in settings:
            if key in raw:
                settings[key] = bool(raw[key])
    return bool(settings.get(category, True))


def recipients(config: dict[str, Any], config_exists: bool) -> list[str]:
    values = config.get("notification_recipients") if isinstance(config, dict) else None
    if isinstance(values, list):
        result = sorted({str(value) for value in values if str(value)})
        if result or config.get("owner_id"):
            return result
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if fallback and (not config_exists or not config.get("owner_id")):
        return [fallback]
    return []


def install(monitor_module: Any) -> None:
    def routed_send_message(
        text: str,
        url: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        config, exists = load_config()
        category = classify(text)
        if not enabled_for(config, category):
            print(f"Notification suppressed by setting: {category}")
            return {"ok": True, "result": {"suppressed": True, "category": category}}

        targets = recipients(config, exists)
        if not targets:
            print(f"Notification has no recipients: {category}")
            return {"ok": True, "result": {"suppressed": True, "category": category}}

        result: dict = {"ok": True, "result": {"sent": 0}}
        errors: list[str] = []
        for chat_id in targets:
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            elif url:
                payload["reply_markup"] = {
                    "inline_keyboard": [[{"text": "Открыть колесо", "url": url}]]
                }
            try:
                response = monitor_module.telegram_api("sendMessage", payload)
                result = response
                if isinstance(result.get("result"), dict):
                    result["result"]["routed_to"] = chat_id
            except Exception as exc:
                errors.append(f"{chat_id}:{type(exc).__name__}")
                print(f"WARNING notification target {chat_id}: {type(exc).__name__}: {exc}")
        if errors and len(errors) == len(targets):
            raise RuntimeError("All notification targets failed: " + ", ".join(errors))
        return result

    monitor_module.send_message = routed_send_message


def self_test() -> None:
    assert classify("📊 Ежедневный отчёт BetBoom") == "daily_reports"
    assert classify("📭 За 7 дней колёса не обнаружены") == "weekly_reports"
    assert classify("🤖 Автоматический монитор работает") == "service_notifications"
    assert classify("🎡 Новое колесо") == "wheel_notifications"
    print("notification_router self-test passed")


if __name__ == "__main__":
    self_test()
