from __future__ import annotations

import html
import re
from typing import Any

from bbvg.ai_core import AIClient, FEATURE_NATURAL_LANGUAGE_ADMIN, client_from_env

READ_ONLY_INTENTS = frozenset(
    {
        "show_active_wheels",
        "show_system_status",
        "show_source_ranking",
        "show_inactive_sources",
        "show_sources",
        "show_source_detail",
        "show_profile",
    }
)
WRITE_INTENTS = frozenset({"set_monitor_interval", "set_source_mode"})
BLOCKED_INTENTS = frozenset(
    {
        "delete_user_data",
        "mass_delete_users",
        "transfer_owner",
        "manage_secrets",
        "rewrite_git_history",
        "delete_backups",
        "unknown_critical_action",
    }
)
ALLOWED_INTENTS = READ_ONLY_INTENTS | WRITE_INTENTS | BLOCKED_INTENTS | {"unknown"}
ALLOWED_INTERVALS = frozenset({1, 3, 5, 10, 15, 30})
ALLOWED_SOURCE_MODES = frozenset({"fast", "nightly", "remove"})
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
SOURCE_IN_TEXT_RE = re.compile(
    r"(?:@|https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?)?([A-Za-z0-9_]{5,32})",
    re.I,
)


def _clean_source(value: object) -> str:
    source = str(value or "").strip()
    source = re.sub(
        r"^https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?",
        "",
        source,
        flags=re.I,
    )
    source = source.split("?", 1)[0].split("/", 1)[0].strip().lstrip("@")
    return source if USERNAME_RE.fullmatch(source) else ""


def _normalize_result(data: dict[str, Any] | None) -> dict[str, Any]:
    value = data if isinstance(data, dict) else {}
    intent = str(value.get("action") or value.get("intent") or "unknown").strip().casefold()
    if intent not in ALLOWED_INTENTS:
        intent = "unknown"
    arguments = value.get("arguments") if isinstance(value.get("arguments"), dict) else {}
    try:
        confidence = float(value.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "intent": intent,
        "arguments": arguments,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(value.get("reason") or "").strip()[:400],
    }


def _extract_source(text: str) -> str:
    explicit = re.search(
        r"(?:@|https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?)"
        r"([A-Za-z0-9_]{5,32})",
        text,
        flags=re.I,
    )
    if explicit:
        return _clean_source(explicit.group(1))
    words = re.findall(r"[A-Za-z0-9_]{5,32}", text)
    stop = {
        "betboom",
        "telegram",
        "source",
        "sources",
        "fast",
        "nightly",
        "monitor",
    }
    for word in reversed(words):
        if word.casefold() not in stop:
            cleaned = _clean_source(word)
            if cleaned:
                return cleaned
    return ""


def _rule_parse(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").casefold().replace("ё", "е").split())
    if not lowered:
        return None

    critical_patterns = (
        ("перепиш", "истори", "git", "rewrite_git_history"),
        ("удал", "бэкап", "delete_backups"),
        ("удал", "backup", "delete_backups"),
        ("секрет", "manage_secrets", "manage_secrets", "manage_secrets"),
        ("переда", "владель", "transfer_owner", "transfer_owner"),
    )
    for first, second, _, intent in critical_patterns:
        if first in lowered and second in lowered:
            return {
                "status": "rules",
                "intent": intent,
                "arguments": {},
                "confidence": 1.0,
                "reason": "blocked by deterministic policy",
            }

    interval = re.search(
        r"(?:интервал|провер(?:к|я)|кажд\w*|раз\s+в)\D{0,24}(1|3|5|10|15|30)\s*(?:мин|минут)",
        lowered,
    )
    if interval:
        return {
            "status": "rules",
            "intent": "set_monitor_interval",
            "arguments": {"minutes": int(interval.group(1))},
            "confidence": 1.0,
            "reason": "deterministic interval command",
        }

    source = _extract_source(text)
    if source and any(word in lowered for word in ("добав", "перенес", "перевед", "постав", "убер", "удал")):
        if any(word in lowered for word in ("основн", "fast", "быстр")):
            mode = "fast"
        elif any(word in lowered for word in ("ночн", "nightly")):
            mode = "nightly"
        elif any(word in lowered for word in ("убер", "удал", "исключ")):
            mode = "remove"
        else:
            mode = ""
        if mode:
            return {
                "status": "rules",
                "intent": "set_source_mode",
                "arguments": {"source": source, "mode": mode},
                "confidence": 1.0,
                "reason": "deterministic source command",
            }

    if any(phrase in lowered for phrase in ("активные колеса", "активных колес", "что сейчас активно")):
        return {"status": "rules", "intent": "show_active_wheels", "arguments": {}, "confidence": 1.0}
    if any(phrase in lowered for phrase in ("статус системы", "работа системы", "как работает бот", "состояние системы")):
        return {"status": "rules", "intent": "show_system_status", "arguments": {}, "confidence": 1.0}
    if any(phrase in lowered for phrase in ("рейтинг источников", "лучшие источники", "лучший источник", "кто лучший")):
        return {"status": "rules", "intent": "show_source_ranking", "arguments": {}, "confidence": 1.0}
    if any(phrase in lowered for phrase in ("давно без колес", "неактивные источники", "источники без колес")):
        return {"status": "rules", "intent": "show_inactive_sources", "arguments": {}, "confidence": 1.0}
    if any(phrase in lowered for phrase in ("мой профиль", "покажи профиль", "моя статистика")):
        return {"status": "rules", "intent": "show_profile", "arguments": {}, "confidence": 1.0}
    if source and any(phrase in lowered for phrase in ("покажи источник", "что с источником", "информация об источнике", "почему источник")):
        return {
            "status": "rules",
            "intent": "show_source_detail",
            "arguments": {"source": source},
            "confidence": 1.0,
        }
    if any(phrase in lowered for phrase in ("покажи источники", "список источников", "все источники")):
        return {"status": "rules", "intent": "show_sources", "arguments": {}, "confidence": 1.0}
    return None


def parse_request(text: str, *, client: AIClient | None = None) -> dict[str, Any]:
    deterministic = _rule_parse(text)
    if deterministic is not None:
        return deterministic

    ai = client or client_from_env()
    if not ai.feature_enabled(FEATURE_NATURAL_LANGUAGE_ADMIN):
        return {"status": "disabled", "intent": "unknown", "arguments": {}, "confidence": 0.0}
    if not ai.status_snapshot().get("provider_configured"):
        return {"status": "not_configured", "intent": "unknown", "arguments": {}, "confidence": 0.0}

    result = ai.ask_json(
        FEATURE_NATURAL_LANGUAGE_ADMIN,
        system_prompt=(
            "Convert one Russian administrator message for the BB V.G. Telegram control center into a strict intent. "
            "Allowed actions: show_active_wheels, show_system_status, show_source_ranking, show_inactive_sources, "
            "show_sources, show_source_detail, show_profile, set_monitor_interval, set_source_mode, "
            "delete_user_data, mass_delete_users, transfer_owner, manage_secrets, rewrite_git_history, delete_backups, "
            "unknown_critical_action, unknown. "
            "For set_monitor_interval put integer minutes in arguments.minutes. "
            "For show_source_detail put Telegram username in arguments.source. "
            "For set_source_mode put username in arguments.source and mode fast, nightly, or remove in arguments.mode. "
            "Never invent another action. Return action, arguments, confidence, reason."
        ),
        user_input=str(text or "")[:1200],
        fallback_data={"action": "unknown", "arguments": {}, "confidence": 0.0, "reason": "analysis unavailable"},
    )
    if not result.ok:
        return {
            "status": result.status,
            "intent": "unknown",
            "arguments": {},
            "confidence": 0.0,
            "decision_id": result.decision_id,
        }
    normalized = _normalize_result(result.data)
    normalized["status"] = "ok"
    normalized["decision_id"] = result.decision_id
    return normalized


def _reply_markup(callback_data: str, cancel_data: str = "page:menu") -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": callback_data},
                {"text": "Отмена", "callback_data": cancel_data},
            ]
        ]
    }


def _read_only(runtime: Any, intent: str, arguments: dict[str, Any]) -> bool:
    if intent == "show_active_wheels":
        runtime.show_active()
    elif intent == "show_system_status":
        runtime.show_status()
    elif intent == "show_source_ranking":
        runtime.show_ranking()
    elif intent == "show_inactive_sources":
        runtime.show_inactive_report(0)
    elif intent == "show_sources":
        runtime.show_sources()
    elif intent == "show_profile" and hasattr(runtime, "show_profile"):
        runtime.show_profile()
    elif intent == "show_source_detail":
        source = _clean_source(arguments.get("source"))
        if not source:
            runtime.send("Не удалось определить username источника.", reply_markup=runtime.with_nav())
        else:
            runtime.show_source_detail(source)
    else:
        return False
    return True


def handle_text(runtime: Any, message: dict[str, Any], *, client: AIClient | None = None) -> bool:
    text = str(message.get("text") or "").strip()
    if not text or text.startswith("/") or len(text) > 1200:
        return False
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    user_id = str(sender.get("id") or "")
    if not user_id:
        return False

    pending = getattr(runtime, "pending_input", {}).get(int(user_id)) if user_id.isdigit() else None
    if pending:
        return False

    try:
        runtime.set_context(chat.get("id"), sender.get("id"))
        role = runtime.role_for(user_id)
    except Exception:
        return False
    if role not in {"owner", "admin"}:
        return False

    parsed = parse_request(text, client=client)
    if parsed.get("status") not in {"ok", "rules"} or float(parsed.get("confidence", 0.0) or 0.0) < 0.72:
        return False

    intent = str(parsed.get("intent") or "unknown")
    arguments = parsed.get("arguments") if isinstance(parsed.get("arguments"), dict) else {}
    if intent in READ_ONLY_INTENTS:
        return _read_only(runtime, intent, arguments)

    if intent in BLOCKED_INTENTS:
        runtime.send(
            "⛔ Эта операция не выполняется через AI-команды. Используйте штатный защищённый интерфейс управления.",
            reply_markup=runtime.with_nav(),
        )
        return True

    if intent == "set_monitor_interval":
        try:
            minutes = int(arguments.get("minutes"))
        except (TypeError, ValueError):
            minutes = 0
        if minutes not in ALLOWED_INTERVALS:
            runtime.send(
                "Допустимый интервал: 1, 3, 5, 10, 15 или 30 минут.",
                reply_markup=runtime.with_nav(),
            )
            return True
        runtime.send(
            f"Подтвердить изменение интервала проверки на <b>{minutes} мин.</b>?",
            reply_markup=_reply_markup(f"nladmin:interval:{minutes}", "page:settings"),
        )
        return True

    if intent == "set_source_mode":
        source = _clean_source(arguments.get("source"))
        mode = str(arguments.get("mode") or "").strip().casefold()
        if not source or mode not in ALLOWED_SOURCE_MODES:
            runtime.send(
                "Не удалось безопасно определить источник или режим.",
                reply_markup=runtime.with_nav(),
            )
            return True
        label = {
            "fast": "основной мониторинг",
            "nightly": "ночной мониторинг",
            "remove": "удаление из мониторинга",
        }[mode]
        runtime.send(
            f"Подтвердить для <b>@{html.escape(source)}</b>: {label}?",
            reply_markup=_reply_markup(f"nladmin:source:{mode}:{source}", f"sd:{source}"),
        )
        return True

    return False


def handle_callback(runtime: Any, query: dict[str, Any]) -> bool:
    data = str(query.get("data") or "")
    if not data.startswith("nladmin:"):
        return False
    runtime._prepare_callback_user(query)
    query_id = str(query.get("id") or "")
    if not runtime.is_admin():
        runtime.answer(query_id, "Недоступно")
        return True

    try:
        if data.startswith("nladmin:interval:"):
            minutes = int(data.rsplit(":", 1)[1])
            if minutes not in ALLOWED_INTERVALS:
                raise ValueError("Недопустимый интервал")
            runtime.set_interval(minutes)
            runtime.answer(query_id, "Настройка сохранена")
            runtime.send(
                f"✅ Интервал проверки установлен: {minutes} мин.",
                reply_markup=runtime.with_nav(),
            )
            return True

        if data.startswith("nladmin:source:"):
            _, _, mode, source = data.split(":", 3)
            source = _clean_source(source)
            if not source or mode not in ALLOWED_SOURCE_MODES:
                raise ValueError("Некорректные параметры")
            if mode != "remove":
                available, detail = runtime.verify_public_source(source)
                if not available:
                    runtime.answer(query_id, "Источник не добавлен")
                    runtime.send(
                        f"⚠️ @{html.escape(source)}: {html.escape(detail)}.",
                        reply_markup=runtime.with_nav(),
                    )
                    return True
            result = runtime.set_source_mode(source, mode)
            runtime.answer(query_id, "Изменение выполнено")
            runtime.send(f"✅ {html.escape(result)}", reply_markup=runtime.with_nav())
            return True
    except Exception as exc:
        print(f"ERROR natural-language admin confirmation: {type(exc).__name__}: {exc}")
        runtime.answer(query_id, "Не удалось выполнить")
        runtime.send("⚠️ Команда не выполнена.", reply_markup=runtime.with_nav())
        return True
    return True


def install(runtime_cls: type) -> None:
    if getattr(runtime_cls, "_bbvg_natural_language_admin_installed", False):
        return
    original_message = runtime_cls.handle_message
    original_callback = runtime_cls.handle_callback

    def handle_message_with_ai(self, message: dict[str, Any]) -> None:
        try:
            if handle_text(self, message):
                return
        except Exception as exc:
            print(f"WARNING natural-language admin parsing failed: {type(exc).__name__}: {exc}")
        original_message(self, message)

    def handle_callback_with_ai(self, query: dict[str, Any]) -> None:
        if handle_callback(self, query):
            return
        original_callback(self, query)

    runtime_cls.handle_message = handle_message_with_ai
    runtime_cls.handle_callback = handle_callback_with_ai
    runtime_cls._bbvg_natural_language_admin_installed = True
