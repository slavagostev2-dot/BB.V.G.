from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

import admin_bot as legacy
from admin_bot import Snapshot
from admin_runtime import RuntimeAdminBot

UTC = timezone.utc
ACCESS_PATH = "bot_access.json"
CACHE_REFRESH_SECONDS = max(10, int(os.getenv("ADMIN_CACHE_SECONDS", "20")))
SOURCE_INACTIVITY_DAYS = max(1, int(os.getenv("SOURCE_INACTIVITY_DAYS", "7")))
MONITOR_INTERVAL_MINUTES = max(1, int(os.getenv("MONITOR_INTERVAL_MINUTES", "5")))
BLOCKED_SOURCES = {"frixa_betboom", "gazazor"}
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
POST_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:telegram\.me|t\.me)/"
    r"(?P<source>[A-Za-z][A-Za-z0-9_]{3,31})/(?P<message>\d+)",
    re.I,
)
WHEEL_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?betboom\.ru/freestream/[A-Za-z0-9._~-]+", re.I)

BTN_STATS = "📊 Статистика"
BTN_ACTIVE = "🔥 Активные колёса"
BTN_SOURCES = "📡 Источники"
BTN_RANKING = "🏆 Рейтинг каналов"
BTN_REPORTS = "📅 Отчёты"
BTN_DISCOVERY = "🔎 Поиск новых источников"
BTN_SETTINGS = "⚙️ Настройки"
BTN_CONTROL = "🛠 Управление"
BTN_STATUS = "✅ Проверка работы"
BTN_MENU = "🏠 Главное меню"

ADMIN_KEYBOARD = {
    "keyboard": [
        [{"text": BTN_STATS}, {"text": BTN_ACTIVE}],
        [{"text": BTN_SOURCES}, {"text": BTN_RANKING}],
        [{"text": BTN_REPORTS}, {"text": BTN_DISCOVERY}],
        [{"text": BTN_SETTINGS}, {"text": BTN_CONTROL}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Панель BetBoom Monitor",
}

USER_KEYBOARD = {
    "keyboard": [
        [{"text": BTN_STATS}, {"text": BTN_ACTIVE}],
        [{"text": BTN_SOURCES}, {"text": BTN_RANKING}],
        [{"text": BTN_REPORTS}, {"text": BTN_STATUS}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "BetBoom Monitor",
}

COMMANDS = [
    {"command": "start", "description": "Открыть панель"},
    {"command": "menu", "description": "Главное меню"},
    {"command": "status", "description": "Проверить работу системы"},
    {"command": "stats", "description": "Статистика"},
    {"command": "active", "description": "Активные колёса"},
    {"command": "sources", "description": "Источники"},
    {"command": "ranking", "description": "Рейтинг каналов"},
    {"command": "reports", "description": "Отчёты"},
    {"command": "myid", "description": "Показать мой Telegram ID"},
]

DEFAULT_SETTINGS = {
    "public_panel": True,
    "notifications": True,
}


def default_access() -> dict[str, Any]:
    return {
        "version": 2,
        "owner_id": "",
        "admins": [],
        "users": {},
        "blocked_users": [],
        "notification_recipients": [],
        "settings": dict(DEFAULT_SETTINGS),
    }


class TelegramPanelV2(RuntimeAdminBot):
    def __init__(self) -> None:
        super().__init__()
        self.current_chat_id: str | None = None
        self.current_user_id: str | None = None
        self.current_role: str = "guest"
        self.navigation: dict[str, list[str]] = {}
        self.snapshot_lock = threading.RLock()
        self.snapshot_value: Snapshot | None = None
        self.snapshot_updated_at = 0.0
        self.refresh_requested = threading.Event()
        self.stop_refresh = threading.Event()
        self.access_lock = threading.RLock()
        self.access = default_access()
        self.access_loaded = False

    # ---------- Telegram / context ----------
    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        target = str(chat_id or self.current_chat_id or legacy.BOT_CHAT_ID)
        payload: dict[str, Any] = {
            "chat_id": target,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.telegram_api("sendMessage", payload)

    def setup_bot(self) -> None:
        self.telegram_api("deleteWebhook", {"drop_pending_updates": False})
        self.telegram_api("setMyCommands", {"commands": COMMANDS})

    @staticmethod
    def private_chat(message: dict[str, Any]) -> bool:
        chat = message.get("chat") if isinstance(message, dict) else None
        return isinstance(chat, dict) and str(chat.get("type") or "") == "private"

    def set_context(self, chat_id: Any, user_id: Any) -> None:
        self.current_chat_id = str(chat_id) if chat_id is not None else None
        self.current_user_id = str(user_id) if user_id is not None else None
        self.current_role = self.role_for(self.current_user_id)

    def is_admin(self) -> bool:
        return self.current_role in {"owner", "admin"}

    def is_owner(self) -> bool:
        return self.current_role == "owner"

    # ---------- Access configuration ----------
    def normalize_access(self, value: dict[str, Any]) -> dict[str, Any]:
        result = default_access()
        if isinstance(value, dict):
            result.update({k: v for k, v in value.items() if k in result})
        result["owner_id"] = str(result.get("owner_id") or "")
        result["admins"] = sorted({str(x) for x in result.get("admins", []) if str(x)})
        result["blocked_users"] = sorted(
            {str(x) for x in result.get("blocked_users", []) if str(x)}
        )
        result["notification_recipients"] = sorted(
            {str(x) for x in result.get("notification_recipients", []) if str(x)}
        )
        users = result.get("users")
        result["users"] = users if isinstance(users, dict) else {}
        settings = dict(DEFAULT_SETTINGS)
        raw_settings = result.get("settings")
        if isinstance(raw_settings, dict):
            settings["public_panel"] = bool(
                raw_settings.get("public_panel", DEFAULT_SETTINGS["public_panel"])
            )
            settings["notifications"] = bool(
                raw_settings.get(
                    "notifications",
                    raw_settings.get("wheel_notifications", DEFAULT_SETTINGS["notifications"]),
                )
            )
        result["settings"] = settings
        result["version"] = 2
        return result

    def load_access(self, force: bool = False) -> dict[str, Any]:
        with self.access_lock:
            if self.access_loaded and not force:
                return self.access
            try:
                value = self.get_json_file(ACCESS_PATH, default_access())
            except Exception:
                value = default_access()
            self.access = self.normalize_access(value)
            self.access_loaded = True
            return self.access

    def save_access(self, message: str = "Update Telegram panel access") -> None:
        with self.access_lock:
            content = json.dumps(
                self.normalize_access(self.access),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n"
            self.update_file(ACCESS_PATH, content, message)
            self.access = json.loads(content)
            self.access_loaded = True

    def role_for(self, user_id: str | None) -> str:
        if not user_id:
            return "guest"
        access = self.load_access()
        if user_id in access.get("blocked_users", []):
            return "blocked"
        if user_id == str(access.get("owner_id") or ""):
            return "owner"
        if user_id in {str(x) for x in access.get("admins", [])}:
            return "admin"
        if user_id in access.get("users", {}):
            return "user"
        return "guest"

    def register_user(self, message: dict[str, Any]) -> str:
        sender = message.get("from") if isinstance(message, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return "guest"
        user_id = str(sender.get("id") or "")
        chat_id = str(chat.get("id") or "")
        if not user_id:
            return "guest"
        access = self.load_access()
        changed = False
        if not access.get("owner_id"):
            bootstrap_ids = {
                str(legacy.BOT_CHAT_ID or ""),
                str(legacy.ADMIN_USER_ID or ""),
            }
            if user_id in bootstrap_ids or chat_id in bootstrap_ids:
                access["owner_id"] = user_id
                if chat_id not in access["notification_recipients"]:
                    access["notification_recipients"].append(chat_id)
                changed = True
        users = access.setdefault("users", {})
        previous = users.get(user_id, {}) if isinstance(users.get(user_id), dict) else {}
        # Preserve user-scoped state when profile metadata is refreshed.
        record = {
            **previous,
            "id": user_id,
            "chat_id": chat_id,
            "username": str(sender.get("username") or ""),
            "first_name": str(sender.get("first_name") or ""),
            "last_name": str(sender.get("last_name") or ""),
            "last_seen_at": datetime.now(UTC).isoformat(),
            "first_seen_at": str(previous.get("first_seen_at") or datetime.now(UTC).isoformat()),
            "notifications_enabled": bool(
                previous.get(
                    "notifications_enabled",
                    chat_id in {str(value) for value in access.get("notification_recipients", [])},
                )
            ),
        }
        if previous != record:
            users[user_id] = record
            changed = True
        if changed:
            self.save_access("Register Telegram panel user [skip ci]")
        return self.role_for(user_id)

    def can_view(self) -> bool:
        if self.current_role in {"owner", "admin", "user"}:
            if self.current_role == "user" and not self.access["settings"]["public_panel"]:
                return False
            return True
        return False

    # ---------- Fast snapshot cache ----------
    def _direct_get_file(self, path: str) -> str:
        encoded = quote(path, safe="/")
        response = requests.get(
            f"{legacy.GH_API}/repos/{legacy.GITHUB_REPOSITORY}/contents/{encoded}",
            params={"ref": legacy.GITHUB_BRANCH},
            headers=self.gh_headers(),
            timeout=legacy.REQUEST_TIMEOUT + 15,
        )
        response.raise_for_status()
        data = response.json()
        return base64.b64decode(data.get("content", "")).decode("utf-8")

    @staticmethod
    def _json_text(text: str, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return default
        return value if isinstance(value, dict) else default

    def refresh_snapshot(self) -> Snapshot:
        files = {
            "state": "state.json",
            "stats": "source_stats.json",
            "health": "source_health.json",
            "discovery": "discovery_state.json",
            "unknown": "unknown_timer_samples.json",
            "fast": "public_sources.txt",
            "nightly": "source_catalog.txt",
        }
        values: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(files)) as pool:
            futures = {pool.submit(self._direct_get_file, path): key for key, path in files.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    values[key] = future.result()
                except Exception as exc:
                    print(f"WARNING snapshot {key}: {type(exc).__name__}: {exc}")
                    values[key] = ""
        snap = Snapshot(
            state=self._json_text(values.get("state", ""), {}),
            stats=self._json_text(values.get("stats", ""), {"sources": {}, "daily": {}}),
            health=self._json_text(values.get("health", ""), {"sources": {}}),
            discovery=self._json_text(values.get("discovery", ""), {}),
            unknown=self._json_text(values.get("unknown", ""), {"samples": []}),
            fast=self.parse_list(values.get("fast", "")),
            nightly=self.parse_list(values.get("nightly", "")),
        )
        with self.snapshot_lock:
            self.snapshot_value = snap
            self.snapshot_updated_at = time.monotonic()
        return snap

    def snapshot(self, *, force: bool = False) -> Snapshot:
        with self.snapshot_lock:
            current = self.snapshot_value
            age = time.monotonic() - self.snapshot_updated_at
        if force or current is None:
            return self.refresh_snapshot()
        if age >= CACHE_REFRESH_SECONDS:
            self.refresh_requested.set()
        return current

    def refresh_loop(self) -> None:
        while not self.stop_refresh.is_set():
            try:
                self.refresh_snapshot()
            except Exception as exc:
                print(f"WARNING refresh loop: {type(exc).__name__}: {exc}")
            self.refresh_requested.wait(CACHE_REFRESH_SECONDS)
            self.refresh_requested.clear()

    # ---------- Navigation ----------
    def stack(self) -> list[str]:
        key = str(self.current_user_id or "guest")
        return self.navigation.setdefault(key, [])

    def open_page(self, page: str, *, push: bool = True) -> None:
        stack = self.stack()
        if push:
            if not stack or stack[-1] != page:
                stack.append(page)
        self.render_page(page)

    def back(self) -> None:
        stack = self.stack()
        if len(stack) > 1:
            stack.pop()
            self.render_page(stack[-1])
        else:
            self.show_menu(clear_stack=True)

    def nav_rows(self) -> list[list[dict[str, str]]]:
        return [
            [
                {"text": "⬅️ Назад", "callback_data": "nav:back"},
                {"text": "🏠 Главное меню", "callback_data": "nav:home"},
            ]
        ]

    def with_nav(self, rows: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        return {"inline_keyboard": list(rows or []) + self.nav_rows()}

    def render_page(self, page: str) -> None:
        if page == "menu":
            self.show_menu(clear_stack=False)
        elif page.startswith("stats:"):
            self.show_stats(int(page.split(":", 1)[1]))
        elif page == "active":
            self.show_active()
        elif page == "sources":
            self.show_sources()
        elif page.startswith("source_list:"):
            _, group, page_no = page.split(":", 2)
            self.show_source_list(group, int(page_no))
        elif page.startswith("source_detail:"):
            self.show_source_detail(page.split(":", 1)[1])
        elif page == "ranking":
            self.show_ranking()
        elif page == "reports":
            self.show_reports()
        elif page.startswith("report:"):
            value = page.split(":", 1)[1]
            if value.isdigit():
                self.show_period_report(int(value))
            elif value == "inactive":
                self.show_inactive_report()
            elif value == "errors":
                self.show_errors_report()
        elif page == "discovery":
            self.show_discovery()
        elif page == "settings":
            self.show_settings()
        elif page == "recipients":
            self.show_recipients()
        elif page == "access":
            self.show_access()
        elif page.startswith("user:"):
            self.show_user_detail(page.split(":", 1)[1])
        elif page == "control":
            self.show_control()
        elif page == "status":
            self.show_status()
        elif page == "diagnostic":
            self.show_diagnostic()
        else:
            self.show_menu(clear_stack=True)

    # ---------- Shared presentation ----------
    @staticmethod
    def bool_mark(value: bool) -> str:
        return "✅" if value else "❌"

    @staticmethod
    def role_name(role: str) -> str:
        return {
            "owner": "владелец",
            "admin": "администратор",
            "user": "пользователь",
            "blocked": "заблокирован",
            "guest": "гость",
        }.get(role, role)

    @staticmethod
    def source_mode_name(mode: str) -> str:
        return {
            "primary": "Основные источники",
            "reserve": "Резервная проверка",
            "paused": "Временно приостановлены",
            "quiet": "Давно без колёс",
            "fast": "Основная проверка",
            "nightly": "Резервная проверка",
        }.get(mode, mode)

    @staticmethod
    def source_status_name(value: str) -> str:
        return {
            "ok": "работает",
            "quarantined": "временно приостановлен",
            "error": "ошибка проверки",
            "empty": "публичные сообщения не найдены",
            "unknown": "нет данных",
        }.get(value, value or "нет данных")

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        keyboard = ADMIN_KEYBOARD if role in {"owner", "admin"} else USER_KEYBOARD
        title = "панель управления" if role in {"owner", "admin"} else "информационная панель"
        text = (
            f"🎡 <b>BetBoom Monitor — {title}</b>\n\n"
            f"Ваш доступ: <b>{self.role_name(role)}</b>\n"
            f"Ваш Telegram ID: <code>{html.escape(str(self.current_user_id or ''))}</code>\n\n"
            "Выберите раздел на постоянной клавиатуре ниже."
        )
        self.send(text, reply_markup=keyboard)

    def show_status(self) -> None:
        snap = self.snapshot()
        state = snap.state
        last = state.get("last_heartbeat_at")
        health_entries = [
            entry for entry in snap.health.get("sources", {}).values() if isinstance(entry, dict)
        ]
        ok = sum(1 for entry in health_entries if entry.get("status") == "ok")
        paused = sum(1 for entry in health_entries if entry.get("status") == "quarantined")
        problems = max(0, len(health_entries) - ok - paused)
        age = self.age_text(last)
        working = "🟢 работает" if self.parse_dt(last) and datetime.now(UTC) - self.parse_dt(last) < timedelta(hours=2) else "🟡 требуется проверка"
        text = (
            "✅ <b>Проверка работы системы</b>\n\n"
            f"Монитор: <b>{working}</b>\n"
            f"Последняя подтверждённая проверка: {self.fmt_dt(last)} ({age})\n"
            f"Основных источников: {len(snap.fast)}\n"
            f"Резервных источников: {len(snap.nightly)}\n"
            f"Работают нормально: {ok}\n"
            f"Временно приостановлены: {paused}\n"
            f"С ошибками: {problems}\n"
            f"Активных колёс: {len(state.get('active_wheels', {}))}"
        )
        rows = [[{"text": "🔄 Обновить данные", "callback_data": "refresh:status"}]]
        if self.is_admin():
            rows.append([{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}])
        self.send(text, reply_markup=self.with_nav(rows))

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot()
        totals = self.period_totals(snap.stats, days)
        title = "сегодня" if days == 1 else f"за {days} дней"
        text = (
            f"📊 <b>Статистика {title}</b>\n\n"
            f"Проверок источников: {totals.get('checks', 0)}\n"
            f"Просмотрено сообщений: {totals.get('messages_scanned', 0)}\n"
            f"Найдено постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Отправлено первых уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Подтверждено активных колёс: {totals.get('activation_sent', 0)}\n"
            f"Повторные уведомления подавлены: {totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок проверки: {totals.get('errors', 0)}\n\n"
            f"Сейчас активных колёс: {len(snap.state.get('active_wheels', {}))}\n"
            f"Ожидают подтверждения: {len(snap.state.get('pending_posts', {}))}"
        )
        rows = [[
            {"text": "Сегодня", "callback_data": "page:stats:1"},
            {"text": "7 дней", "callback_data": "page:stats:7"},
            {"text": "30 дней", "callback_data": "page:stats:30"},
        ]]
        self.send(text, reply_markup=self.with_nav(rows))

    def active_rows(self, snap: Snapshot) -> list[tuple[str, dict[str, Any]]]:
        rows = [
            (str(key), entry)
            for key, entry in snap.state.get("active_wheels", {}).items()
            if isinstance(entry, dict)
        ]
        rows.sort(
            key=lambda item: (
                self.parse_dt(item[1].get("deadline")) is None,
                self.parse_dt(item[1].get("deadline")) or datetime.max.replace(tzinfo=UTC),
                item[0],
            )
        )
        return rows

    def show_active(self) -> None:
        snap = self.snapshot()
        rows = self.active_rows(snap)
        lines = [f"🔥 <b>Активные колёса: {len(rows)}</b>", ""]
        keyboard: list[list[dict[str, str]]] = []
        participating = {str(x).casefold() for x in snap.state.get("participating_wheels", {})}
        for index, (key, entry) in enumerate(rows[:20], 1):
            identifier = str(entry.get("identifier") or key)
            source = str(entry.get("source") or "неизвестно")
            deadline = self.parse_dt(entry.get("deadline"))
            timing = self.remaining(deadline) if deadline else "время не найдено"
            lines.append(
                f"{index}. <code>{html.escape(identifier)}</code> — {html.escape(timing)}\n"
                f"   источник: @{html.escape(source)}"
            )
            row: list[dict[str, str]] = []
            if entry.get("url"):
                row.append({"text": f"🎡 {identifier[:18]}", "url": str(entry["url"])})
            if self.is_admin() and key.casefold() not in participating:
                row.append({"text": "✅ Участвую", "callback_data": f"wheel:part:{key}"})
            if row:
                keyboard.append(row)
            if self.is_admin():
                keyboard.append([
                    {"text": "🔄 Проверить", "callback_data": f"wheel:check:{key}"},
                    {"text": "🗑 Убрать", "callback_data": f"wheel:removeask:{key}"},
                ])
        if not rows:
            lines.append("Активных колёс сейчас нет.")
        keyboard.append([{"text": "🔄 Обновить данные", "callback_data": "refresh:active"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(keyboard))

    @staticmethod
    def remaining(deadline: datetime | None) -> str:
        if not deadline:
            return "не определено"
        seconds = int((deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds())
        if seconds <= 0:
            return "время вышло"
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f"{hours} ч. {minutes} мин." if hours else f"{max(1, minutes)} мин."

    def source_sets(self, snap: Snapshot) -> dict[str, list[str]]:
        health = snap.health.get("sources", {})
        paused = sorted(
            [name for name, entry in health.items() if isinstance(entry, dict) and entry.get("status") == "quarantined"],
            key=str.casefold,
        )
        quiet: list[str] = []
        now = datetime.now(UTC)
        stats_sources = snap.stats.get("sources", {})
        for source in snap.fast:
            entry = stats_sources.get(source, {})
            if not isinstance(entry, dict):
                continue
            reference = self.parse_dt(entry.get("last_wheel_post_at")) or self.parse_dt(entry.get("first_checked_at"))
            if reference and now - reference >= timedelta(days=SOURCE_INACTIVITY_DAYS):
                quiet.append(source)
        return {
            "primary": snap.fast,
            "reserve": snap.nightly,
            "paused": paused,
            "quiet": sorted(quiet, key=str.casefold),
        }

    def show_sources(self) -> None:
        snap = self.snapshot()
        groups = self.source_sets(snap)
        text = (
            "📡 <b>Источники</b>\n\n"
            f"Основная проверка каждые {MONITOR_INTERVAL_MINUTES} минут: {len(groups['primary'])}\n"
            f"Резервная проверка: {len(groups['reserve'])}\n"
            f"Временно приостановлены: {len(groups['paused'])}\n"
            f"Без колёс {SOURCE_INACTIVITY_DAYS}+ дней: {len(groups['quiet'])}"
        )
        rows = [
            [
                {"text": f"⚡ Основные ({len(groups['primary'])})", "callback_data": "source_list:primary:0"},
                {"text": f"🌙 Резервные ({len(groups['reserve'])})", "callback_data": "source_list:reserve:0"},
            ],
            [
                {"text": f"⏸ Приостановлены ({len(groups['paused'])})", "callback_data": "source_list:paused:0"},
                {"text": f"📭 Давно без колёс ({len(groups['quiet'])})", "callback_data": "source_list:quiet:0"},
            ],
        ]
        if self.is_admin():
            rows.append([{"text": "➕ Добавить источник", "callback_data": "source:add"}])
        self.send(text, reply_markup=self.with_nav(rows))

    def show_source_list(self, group: str, page: int = 0) -> None:
        snap = self.snapshot()
        rows = self.source_sets(snap).get(group, [])
        per_page = 10
        page = max(0, min(page, max(0, (len(rows) - 1) // per_page)))
        part = rows[page * per_page : (page + 1) * per_page]
        lines = [f"📡 <b>{html.escape(self.source_mode_name(group))}</b>", ""]
        keyboard: list[list[dict[str, str]]] = []
        stats_sources = snap.stats.get("sources", {})
        for source in part:
            wheels = self.counter(stats_sources.get(source, {}), "wheel_posts")
            lines.append(f"• @{html.escape(source)} — колёс: {wheels}")
            keyboard.append([{"text": f"@{source}", "callback_data": f"source_detail:{source}"}])
        if not part:
            lines.append("Список пуст.")
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "◀️", "callback_data": f"source_list:{group}:{page - 1}"})
        if (page + 1) * per_page < len(rows):
            nav.append({"text": "▶️", "callback_data": f"source_list:{group}:{page + 1}"})
        if nav:
            keyboard.append(nav)
        self.send("\n".join(lines), reply_markup=self.with_nav(keyboard))

    def show_source_detail(self, source: str) -> None:
        source = self.safe_source(source)
        snap = self.snapshot()
        stats = self.merged_source_stats(snap).get(source, {})
        health = snap.health.get("sources", {}).get(source, {})
        discovery = snap.discovery.get("sources", {}).get(source, {})
        primary_set = {x.casefold() for x in snap.fast}
        reserve_set = {x.casefold() for x in snap.nightly}
        mode = "Основная проверка" if source.casefold() in primary_set else (
            "Резервная проверка" if source.casefold() in reserve_set else "Не включён"
        )
        raw_status = str(health.get("status") or discovery.get("status") or "unknown")
        wheels = self.counter(stats, "wheel_posts") or self.counter(discovery, "wheel_links_found")
        score = int(stats.get("quality_score", 0) or 0)
        text = (
            f"📡 <b>@{html.escape(source)}</b>\n\n"
            f"Проверяется: <b>{mode}</b>\n"
            f"Состояние: {html.escape(self.source_status_name(raw_status))}\n"
            f"Проверок: {self.counter(stats, 'checks')}\n"
            f"Постов с колёсами: {wheels}\n"
            f"Очки рейтинга: {score}\n"
            f"Последнее колесо: {self.fmt_dt(stats.get('last_wheel_post_at') or discovery.get('latest_wheel_at'))}\n"
            f"Последняя проверка: {self.fmt_dt(health.get('last_checked_at') or discovery.get('checked_at'))}"
        )
        rows: list[list[dict[str, str]]] = [[{"text": "Открыть Telegram", "url": f"https://telegram.me/{source}"}]]
        if self.is_admin():
            move: list[dict[str, str]] = []
            if mode != "Основная проверка":
                move.append({"text": "⚡ В основные", "callback_data": f"source:move:fast:{source}"})
            if mode != "Резервная проверка":
                move.append({"text": "🌙 В резервные", "callback_data": f"source:move:nightly:{source}"})
            if move:
                rows.append(move)
            if raw_status == "quarantined":
                rows.append([{"text": "▶️ Возобновить проверки", "callback_data": f"source:clearq:{source}"}])
            rows.append([{"text": "🗑 Удалить", "callback_data": f"source:removeask:{source}"}])
        self.send(text, reply_markup=self.with_nav(rows))

    def show_ranking(self) -> None:
        snap = self.snapshot()
        rows: list[tuple[str, int]] = []
        for source, entry in self.merged_source_stats(snap).items():
            score = int(entry.get("quality_score", 0) or 0)
            if score:
                rows.append((source, score))
        rows.sort(key=lambda item: (-item[1], item[0].casefold()))
        lines = ["🏆 <b>Рейтинг каналов</b>", ""]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score) in enumerate(rows[:15], 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(
                f"{mark} <b>@{html.escape(source)}</b> — <b>{score} оч.</b>"
            )
        if not rows:
            lines.append("Рейтинг появится после решения администратора по колесу.")
        self.send("\n".join(lines), reply_markup=self.with_nav([
            [{"text": "🔄 Обновить данные", "callback_data": "refresh:ranking"}]
        ]))

    def show_reports(self) -> None:
        rows = [
            [
                {"text": "Сегодня", "callback_data": "page:report:1"},
                {"text": "7 дней", "callback_data": "page:report:7"},
                {"text": "30 дней", "callback_data": "page:report:30"},
            ],
            [{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}],
            [{"text": "⚠️ Ошибки проверки", "callback_data": "page:report:errors"}],
        ]
        if self.is_admin():
            rows.append([{"text": "📨 Отправить ежедневный отчёт", "callback_data": "control:daily"}])
        self.send("📅 <b>Отчёты</b>\n\nВыберите нужный отчёт.", reply_markup=self.with_nav(rows))

    def show_period_report(self, days: int) -> None:
        snap = self.snapshot()
        totals = self.period_totals(snap.stats, days)
        text = (
            f"📅 <b>Отчёт за {days} дн.</b>\n\n"
            f"Проверок: {totals.get('checks', 0)}\n"
            f"Найдено постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Отправлено уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Подтверждено активаций: {totals.get('activation_sent', 0)}\n"
            f"Повторов подавлено: {totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок: {totals.get('errors', 0)}"
        )
        self.send(text, reply_markup=self.with_nav())

    def show_inactive_report(self) -> None:
        snap = self.snapshot()
        rows = self.source_sets(snap)["quiet"]
        stats = snap.stats.get("sources", {})
        lines = [f"📭 <b>Без колёс {SOURCE_INACTIVITY_DAYS}+ дней: {len(rows)}</b>", ""]
        for source in rows[:40]:
            entry = stats.get(source, {})
            lines.append(f"• @{html.escape(source)} — {self.fmt_dt(entry.get('last_wheel_post_at') or entry.get('first_checked_at'))}")
        if not rows:
            lines.append("Таких каналов сейчас нет.")
        self.send("\n".join(lines), reply_markup=self.with_nav())

    def show_errors_report(self) -> None:
        snap = self.snapshot()
        rows: list[tuple[str, dict[str, Any]]] = []
        for source, entry in snap.health.get("sources", {}).items():
            if isinstance(entry, dict) and entry.get("status") != "ok":
                rows.append((source, entry))
        rows.sort(key=lambda item: (item[1].get("status") != "quarantined", item[0].casefold()))
        lines = [f"⚠️ <b>Источники с проблемами: {len(rows)}</b>", ""]
        for source, entry in rows[:35]:
            reason = str(
                entry.get("failure_reason")
                or entry.get("last_error")
                or self.source_status_name(str(entry.get("status") or ""))
            )
            lines.append(f"• @{html.escape(source)} — {html.escape(reason[:90])}")
        if not rows:
            lines.append("Проблемных источников нет.")
        self.send("\n".join(lines), reply_markup=self.with_nav())

    def show_discovery(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot()
        lines = [
            "🔎 <b>Поиск новых источников</b>",
            "",
            f"Последний запуск: {self.fmt_dt(snap.discovery.get('last_run_at'))}",
            f"Источников в резервной проверке: {len(snap.nightly)}",
            f"Ошибок последнего запуска: {self.counter(snap.discovery, 'error_count')}",
        ]
        rows = [
            [{"text": "▶️ Запустить поиск", "callback_data": "control:nightly"}],
            [{"text": "🌙 Резервные источники", "callback_data": "source_list:reserve:0"}],
        ]
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    # ---------- Settings, roles, recipients ----------
    def show_settings(self) -> None:
        if not self.is_admin():
            self.send("Настройки доступны только администраторам.", reply_markup=self.with_nav())
            return
        settings = self.load_access().get("settings", {})
        text = (
            "⚙️ <b>Настройки</b>\n\n"
            f"Уведомления пользователям: {self.bool_mark(settings['notifications'])}\n"
            "Служебные ошибки получают только владелец и администраторы.\n"
            f"Панель для обычных пользователей: {self.bool_mark(settings['public_panel'])}\n"
            f"Проверка основных источников: каждые {MONITOR_INTERVAL_MINUTES} минут"
        )
        rows = [
            [{"text": f"Уведомления {self.bool_mark(settings['notifications'])}", "callback_data": "setting:notifications"}],
            [{"text": f"Пользовательская панель {self.bool_mark(settings['public_panel'])}", "callback_data": "setting:public_panel"}],
            [{"text": "🔔 Получатели уведомлений", "callback_data": "page:recipients"}],
        ]
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
        self.send(text, reply_markup=self.with_nav(rows))

    def show_recipients(self) -> None:
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        access = self.load_access()
        recipients = {str(x) for x in access.get("notification_recipients", [])}
        users = access.get("users", {})
        lines = ["🔔 <b>Получатели уведомлений</b>", ""]
        rows: list[list[dict[str, str]]] = []
        for user_id, record in sorted(users.items(), key=lambda item: str(item[1].get("first_name") or item[0]).casefold()):
            if not isinstance(record, dict):
                continue
            chat_id = str(record.get("chat_id") or user_id)
            enabled = bool(record.get("notifications_enabled", chat_id in recipients))
            name = " ".join(x for x in [str(record.get("first_name") or ""), str(record.get("last_name") or "")] if x).strip() or str(record.get("username") or user_id)
            lines.append(f"{self.bool_mark(enabled)} {html.escape(name)} — <code>{html.escape(user_id)}</code>")
            rows.append([{"text": f"{self.bool_mark(enabled)} {name[:24]}", "callback_data": f"recipient:{user_id}"}])
        if not rows:
            lines.append("Пользователи ещё не запускали бота.")
        rows.append([{"text": "🔄 Обновить список", "callback_data": "page:recipients"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_access(self) -> None:
        if not self.is_owner():
            self.send("Управление доступом доступно только владельцу.", reply_markup=self.with_nav())
            return
        access = self.load_access()
        owner_id = str(access.get("owner_id") or "")
        admins = {str(x) for x in access.get("admins", [])}
        users = access.get("users", {})
        lines = ["👥 <b>Доступ к панели</b>", "", f"Владелец: <code>{html.escape(owner_id or 'не назначен')}</code>", f"Администраторов: {len(admins)}", f"Известных пользователей: {len(users)}", ""]
        rows: list[list[dict[str, str]]] = []
        for user_id, record in sorted(users.items(), key=lambda item: str(item[1].get("first_name") or item[0]).casefold()):
            role = self.role_for(str(user_id))
            name = str(record.get("first_name") or record.get("username") or user_id) if isinstance(record, dict) else str(user_id)
            lines.append(f"• {html.escape(name)} — {self.role_name(role)} — <code>{html.escape(str(user_id))}</code>")
            rows.append([{"text": f"{name[:20]} · {self.role_name(role)}", "callback_data": f"page:user:{user_id}"}])
        rows.append([{"text": "🔄 Обновить список", "callback_data": "page:access"}])
        rows.append([{"text": "➕ Добавить администратора по ID", "callback_data": "access:add_admin"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_user_detail(self, user_id: str) -> None:
        if not self.is_owner():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        access = self.load_access()
        record = access.get("users", {}).get(user_id, {})
        role = self.role_for(user_id)
        name = " ".join(x for x in [str(record.get("first_name") or ""), str(record.get("last_name") or "")] if x).strip() or str(record.get("username") or user_id)
        chat_id = str(record.get("chat_id") or user_id)
        receives = bool(
            record.get(
                "notifications_enabled",
                chat_id in {str(x) for x in access.get("notification_recipients", [])},
            )
        )
        text = (
            f"👤 <b>{html.escape(name)}</b>\n\n"
            f"Telegram ID: <code>{html.escape(user_id)}</code>\n"
            f"Роль: {self.role_name(role)}\n"
            f"Получает уведомления: {self.bool_mark(receives)}\n"
            f"Последняя активность: {self.fmt_dt(record.get('last_seen_at'))}"
        )
        rows: list[list[dict[str, str]]] = [[{"text": f"Уведомления {self.bool_mark(receives)}", "callback_data": f"recipient:{user_id}"}]]
        if role == "user":
            rows.append([{"text": "Сделать администратором", "callback_data": f"access:promote:{user_id}"}])
        elif role == "admin":
            rows.append([{"text": "Убрать права администратора", "callback_data": f"access:demote:{user_id}"}])
        if role != "owner":
            rows.append([{"text": "👑 Передать владение", "callback_data": f"access:transferask:{user_id}"}])
        self.send(text, reply_markup=self.with_nav(rows))

    def toggle_setting(self, key: str) -> None:
        if not self.is_admin() or key not in DEFAULT_SETTINGS:
            raise PermissionError("Недостаточно прав")
        if key == "public_panel" and not self.is_owner():
            raise PermissionError("Только владелец может менять доступ пользователей")
        access = self.load_access()
        access["settings"][key] = not bool(access["settings"].get(key, DEFAULT_SETTINGS[key]))
        self.save_access(f"Toggle {key} via Telegram panel [skip ci]")
        self.dispatch("monitor.yml", {"continuous": "true"})

    def toggle_recipient(self, user_id: str) -> None:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        access = self.load_access()
        record = access.get("users", {}).get(user_id)
        if not isinstance(record, dict):
            raise ValueError("Пользователь сначала должен запустить бота")
        chat_id = str(record.get("chat_id") or user_id)
        recipients = {str(x) for x in access.get("notification_recipients", [])}
        enabled = bool(record.get("notifications_enabled", chat_id in recipients))
        if enabled:
            recipients.discard(chat_id)
        else:
            recipients.add(chat_id)
        record["notifications_enabled"] = not enabled
        access["notification_recipients"] = sorted(recipients)
        self.save_access("Update notification recipients [skip ci]")
        self.dispatch("monitor.yml", {"continuous": "true"})

    def set_admin(self, user_id: str, enabled: bool) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец управляет администраторами")
        access = self.load_access()
        admins = {str(x) for x in access.get("admins", [])}
        if enabled:
            admins.add(user_id)
            record = access.get("users", {}).get(user_id)
            if isinstance(record, dict):
                chat_id = str(record.get("chat_id") or user_id)
                record["notifications_enabled"] = True
                recipients = {
                    str(value) for value in access.get("notification_recipients", []) if str(value)
                }
                recipients.add(chat_id)
                access["notification_recipients"] = sorted(recipients)
        else:
            admins.discard(user_id)
        admins.discard(str(access.get("owner_id") or ""))
        access["admins"] = sorted(admins)
        self.save_access("Update Telegram administrators [skip ci]")

    def transfer_owner(self, user_id: str) -> None:
        if not self.is_owner():
            raise PermissionError("Только владелец может передать владение")
        access = self.load_access()
        if user_id not in access.get("users", {}):
            raise ValueError("Новый владелец сначала должен запустить бота")
        old_owner = str(access.get("owner_id") or "")
        access["owner_id"] = user_id
        admins = {str(x) for x in access.get("admins", [])}
        admins.discard(user_id)
        if old_owner and old_owner != user_id:
            admins.add(old_owner)
        access["admins"] = sorted(admins)
        self.save_access("Transfer Telegram panel ownership [skip ci]")

    # ---------- Control and diagnostics ----------
    def show_control(self) -> None:
        if not self.is_admin():
            self.send("Управление доступно только администраторам.", reply_markup=self.with_nav())
            return
        rows = [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "🔎 Запустить поиск новых источников", "callback_data": "control:intelligence"}],
            [{"text": "📨 Отправить ежедневный отчёт", "callback_data": "control:daily"}],
            [{"text": "✅ Проверить работу системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]
        self.send("🛠 <b>Управление</b>\n\nВыберите действие.", reply_markup=self.with_nav(rows))

    def show_diagnostic(self) -> None:
        if not self.is_admin():
            self.send("Диагностика доступна администраторам.", reply_markup=self.with_nav())
            return
        self.pending_input[int(self.current_user_id or 0)] = {"kind": "diagnostic"}
        text = (
            "🔍 <b>Почему не пришло колесо?</b>\n\n"
            "Отправьте ссылку на конкретный Telegram-пост или username канала.\n\n"
            "Лучший вариант: <code>https://telegram.me/channel/123</code>"
        )
        self.send(text, reply_markup=self.with_nav())

    def diagnose_input(self, value: str) -> str:
        match = POST_URL_RE.search(value)
        source = match.group("source") if match else self.safe_source(value)
        message_id = int(match.group("message")) if match else None
        if not USERNAME_RE.fullmatch(source):
            return "Не удалось определить публичный username. Отправьте ссылку на пост или username канала."
        snap = self.snapshot()
        primary = {x.casefold() for x in snap.fast}
        reserve = {x.casefold() for x in snap.nightly}
        mode = "основная проверка каждые 5 минут" if source.casefold() in primary else (
            "резервная проверка" if source.casefold() in reserve else "не добавлен в мониторинг"
        )
        url = f"https://telegram.me/s/{source}"
        response = requests.get(url, headers={"User-Agent": legacy.USER_AGENT if hasattr(legacy, 'USER_AGENT') else 'Mozilla/5.0'}, timeout=legacy.REQUEST_TIMEOUT)
        if response.status_code != 200:
            return f"@{html.escape(source)}: Telegram вернул HTTP {response.status_code}. Режим: {mode}."
        soup = BeautifulSoup(response.text, "html.parser")
        nodes = soup.select("div.tgme_widget_message[data-post]")
        selected = None
        wheel_links: list[str] = []
        for node in nodes:
            data_post = str(node.get("data-post") or "")
            node_id = int(data_post.rsplit("/", 1)[1]) if "/" in data_post and data_post.rsplit("/", 1)[1].isdigit() else 0
            text_node = node.select_one("div.tgme_widget_message_text")
            parts = [text_node.get_text("\n", strip=True) if text_node else ""]
            parts.extend(str(a.get("href") or "") for a in node.select("a[href]"))
            body = "\n".join(parts)
            links = WHEEL_LINK_RE.findall(body)
            if message_id is not None and node_id == message_id:
                selected = (node_id, body, links)
                break
            if message_id is None and links:
                selected = (node_id, body, links)
        if selected is None:
            return (
                f"🔍 <b>@{html.escape(source)}</b>\n\n"
                f"Режим: {mode}\n"
                "Нужный пост или ссылка BetBoom среди доступных публичных сообщений не найдены."
            )
        node_id, body, wheel_links = selected
        state_hits: list[str] = []
        for entry in snap.state.get("pending_posts", {}).values():
            if isinstance(entry, dict) and str(entry.get("source") or "").casefold() == source.casefold() and int(entry.get("message_id") or 0) == node_id:
                state_hits.append(
                    "пост найден монитором и ожидает подтверждения; "
                    + str(entry.get("reason") or "причина не записана")
                )
        for entry in snap.state.get("active_wheels", {}).values():
            if isinstance(entry, dict) and str(entry.get("source") or "").casefold() == source.casefold() and int(entry.get("message_id") or 0) == node_id:
                state_hits.append("колесо находится в активном списке")
        reason = "; ".join(state_hits) if state_hits else (
            "пост ещё не попал в состояние монитора" if source.casefold() in primary | reserve else "канал не включён в мониторинг"
        )
        links_text = ", ".join(f"<code>{html.escape(x)}</code>" for x in wheel_links[:3]) or "не распознаны"
        return (
            f"🔍 <b>Диагностика @{html.escape(source)}/{node_id}</b>\n\n"
            f"Режим: {mode}\n"
            f"Ссылки колёс: {links_text}\n"
            f"Результат: {html.escape(reason)}"
        )

    # ---------- Source operations ----------
    def request_add_source(self) -> None:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        self.pending_input[int(self.current_user_id or 0)] = {"kind": "add_source"}
        self.send(
            "➕ Отправьте публичный username Telegram-канала без ссылки.\n\n"
            "Например: <code>example_channel</code>",
            reply_markup=self.with_nav(),
        )

    # ---------- Handlers ----------
    def handle_message(self, message: dict[str, Any]) -> None:
        if not self.private_chat(message):
            return
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        self.set_context(chat_id, user_id)
        self.register_user(message)
        self.set_context(chat_id, user_id)
        if self.current_role == "blocked":
            self.send("Доступ к боту заблокирован.")
            return
        if not self.can_view():
            self.send(
                "Информационная панель сейчас закрыта.\n\n"
                f"Ваш Telegram ID: <code>{html.escape(str(user_id))}</code>"
            )
            return
        text = str(message.get("text") or "").strip()
        if not text:
            return
        command = text.split("@", 1)[0].split(maxsplit=1)[0].casefold()
        if command in {"/start", "/menu"} or text == BTN_MENU:
            self.pending_input.pop(int(user_id), None)
            self.show_menu(clear_stack=True)
            return
        if command == "/myid":
            self.send(f"Ваш Telegram ID: <code>{html.escape(str(user_id))}</code>")
            return
        pending = self.pending_input.get(int(user_id))
        if pending:
            kind = str(pending.get("kind") or "")
            if kind == "diagnostic":
                self.pending_input.pop(int(user_id), None)
                try:
                    result = self.diagnose_input(text)
                except Exception as exc:
                    result = f"⚠️ Диагностика не выполнена: {html.escape(type(exc).__name__)}."
                self.send(result, reply_markup=self.with_nav())
                return
            if kind == "add_admin":
                if not text.isdigit():
                    self.send("Отправьте числовой Telegram ID.")
                    return
                self.set_admin(text, True)
                self.pending_input.pop(int(user_id), None)
                self.send(f"✅ Пользователь <code>{html.escape(text)}</code> назначен администратором.", reply_markup=self.with_nav())
                return
            if kind == "add_source":
                username = self.safe_source(text)
                if username.casefold() in BLOCKED_SOURCES:
                    self.send("Этот источник ранее исключён и защищён от повторного добавления.")
                    return
                if not USERNAME_RE.fullmatch(username):
                    self.send("Некорректный username. Отправьте username без ссылки.")
                    return
                available, detail = self.verify_public_source(username)
                if not available:
                    self.send(f"⚠️ @{html.escape(username)} не добавлен: {html.escape(detail)}.")
                    return
                self.pending_input[int(user_id)] = {"kind": "choose_mode", "source": username}
                self.send(
                    f"✅ @{html.escape(username)} доступен. Выберите режим проверки:",
                    reply_markup=self.with_nav([
                        [
                            {"text": "⚡ Основная проверка", "callback_data": f"source:addmode:fast:{username}"},
                            {"text": "🌙 Резервная проверка", "callback_data": f"source:addmode:nightly:{username}"},
                        ]
                    ]),
                )
                return
        mapping = {
            BTN_STATS: "stats:1",
            BTN_ACTIVE: "active",
            BTN_SOURCES: "sources",
            BTN_RANKING: "ranking",
            BTN_REPORTS: "reports",
            BTN_DISCOVERY: "discovery",
            BTN_SETTINGS: "settings",
            BTN_CONTROL: "control",
            BTN_STATUS: "status",
        }
        command_pages = {
            "/status": "status",
            "/stats": "stats:1",
            "/active": "active",
            "/wheels": "active",
            "/sources": "sources",
            "/ranking": "ranking",
            "/reports": "reports",
        }
        page = mapping.get(text) or command_pages.get(command)
        if page:
            self.navigation[str(user_id)] = ["menu"]
            self.open_page(page)
        else:
            keyboard = ADMIN_KEYBOARD if self.is_admin() else USER_KEYBOARD
            self.send("Команда не распознана. Используйте меню.", reply_markup=keyboard)

    def handle_callback(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        self.set_context(chat_id, user_id)
        if self.current_role == "blocked" or not self.can_view():
            self.answer(query_id, "Недоступно")
            return
        data = str(query.get("data") or "")
        try:
            if data == "nav:back":
                self.answer(query_id, "Назад")
                self.back()
            elif data == "nav:home":
                self.answer(query_id, "Главное меню")
                self.show_menu(clear_stack=True)
            elif data.startswith("page:"):
                page = data.split(":", 1)[1]
                self.answer(query_id, "Открываю")
                self.open_page(page)
            elif data.startswith("refresh:"):
                page = data.split(":", 1)[1]
                self.answer(query_id, "Обновляю")
                self.refresh_snapshot()
                self.render_page(page)
            elif data.startswith("source_list:"):
                _, group, page_no = data.split(":", 2)
                self.answer(query_id, "Открываю")
                self.open_page(f"source_list:{group}:{page_no}")
            elif data.startswith("source_detail:"):
                source = data.split(":", 1)[1]
                self.answer(query_id, "Открываю")
                self.open_page(f"source_detail:{source}")
            elif data == "source:add":
                self.answer(query_id, "Жду username")
                self.request_add_source()
            elif data.startswith("source:addmode:"):
                if not self.is_admin():
                    raise PermissionError
                _, _, mode, source = data.split(":", 3)
                result = self.set_source_mode(source, mode)
                self.pending_input.pop(int(user_id), None)
                self.answer(query_id, "Добавлено")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
            elif data.startswith("source:move:"):
                if not self.is_admin():
                    raise PermissionError
                _, _, mode, source = data.split(":", 3)
                result = self.set_source_mode(source, mode)
                self.answer(query_id, "Изменено")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
            elif data.startswith("source:removeask:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Удалить @{html.escape(source)} из мониторинга?",
                    reply_markup=self.with_nav([[
                        {"text": "Да, удалить", "callback_data": f"source:remove:{source}"},
                        {"text": "Отмена", "callback_data": "nav:back"},
                    ]]),
                )
            elif data.startswith("source:remove:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                result = self.set_source_mode(source, "remove")
                self.answer(query_id, "Удалено")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
            elif data.startswith("source:clearq:"):
                if not self.is_admin():
                    raise PermissionError
                source = data.split(":", 2)[2]
                self.dispatch_admin_action("clear_quarantine", source)
                self.answer(query_id, "Возобновление запущено")
            elif data.startswith("setting:"):
                key = data.split(":", 1)[1]
                self.toggle_setting(key)
                self.answer(query_id, "Настройка изменена")
                self.render_page("settings")
            elif data.startswith("recipient:"):
                target = data.split(":", 1)[1]
                self.toggle_recipient(target)
                self.answer(query_id, "Изменено")
                self.render_page("recipients")
            elif data == "access:add_admin":
                if not self.is_owner():
                    raise PermissionError
                self.pending_input[int(user_id)] = {"kind": "add_admin"}
                self.answer(query_id, "Жду Telegram ID")
                self.send("Отправьте числовой Telegram ID нового администратора.", reply_markup=self.with_nav())
            elif data.startswith("access:promote:"):
                target = data.split(":", 2)[2]
                self.set_admin(target, True)
                self.answer(query_id, "Назначен администратором")
                self.render_page(f"user:{target}")
            elif data.startswith("access:demote:"):
                target = data.split(":", 2)[2]
                self.set_admin(target, False)
                self.answer(query_id, "Права сняты")
                self.render_page(f"user:{target}")
            elif data.startswith("access:transferask:"):
                target = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Передать владение пользователю <code>{html.escape(target)}</code>? Текущий владелец станет администратором.",
                    reply_markup=self.with_nav([[
                        {"text": "Да, передать", "callback_data": f"access:transfer:{target}"},
                        {"text": "Отмена", "callback_data": "nav:back"},
                    ]]),
                )
            elif data.startswith("access:transfer:"):
                target = data.split(":", 2)[2]
                self.transfer_owner(target)
                self.answer(query_id, "Владение передано")
                self.current_role = self.role_for(str(user_id))
                self.show_menu(clear_stack=True)
            elif data.startswith("control:"):
                if not self.is_admin():
                    raise PermissionError
                action = data.split(":", 1)[1]
                workflows = {
                    "monitor": ("monitor.yml", {"continuous": "true"}, "Проверка источников запущена"),
                    "nightly": ("nightly-discovery.yml", None, "Проверка новых кандидатов запущена"),
                    "daily": ("daily-report.yml", None, "Ежедневный отчёт запущен"),
                }
                workflow, inputs, answer = workflows[action]
                self.dispatch(workflow, inputs)
                self.answer(query_id, answer)
                self.send(f"▶️ {html.escape(answer)}.", reply_markup=self.with_nav())
            elif data.startswith("bb:p:") or data.startswith("bb:n:"):
                if not self.is_admin():
                    raise PermissionError
                if data.startswith("bb:p:"):
                    self.dispatch_admin_action("participate_token", data.split(":", 2)[2])
                    self.answer(query_id, "Участие отмечается")
                else:
                    self.answer(query_id, "Участие уже отмечено")
            elif data.startswith("wheel:part:"):
                if not self.is_admin():
                    raise PermissionError
                self.dispatch_admin_action("participate_wheel", data.split(":", 2)[2])
                self.answer(query_id, "Участие отмечается")
            elif data.startswith("wheel:check:"):
                if not self.is_admin():
                    raise PermissionError
                self.dispatch_admin_action("recheck_wheel", data.split(":", 2)[2])
                self.answer(query_id, "Проверка запущена")
            elif data.startswith("wheel:removeask:"):
                if not self.is_admin():
                    raise PermissionError
                key = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Убрать колесо <code>{html.escape(key)}</code> из активного списка?",
                    reply_markup=self.with_nav([[
                        {"text": "Да, убрать", "callback_data": f"wheel:remove:{key}"},
                        {"text": "Отмена", "callback_data": "nav:back"},
                    ]]),
                )
            elif data.startswith("wheel:remove:"):
                if not self.is_admin():
                    raise PermissionError
                self.dispatch_admin_action("remove_active", data.split(":", 2)[2])
                self.answer(query_id, "Удаление запущено")
            else:
                self.answer(query_id, "Неизвестная команда")
        except PermissionError:
            self.answer(query_id, "Недостаточно прав")
        except Exception as exc:
            print(f"ERROR callback {data}: {type(exc).__name__}: {exc}")
            self.answer(query_id, "Ошибка выполнения")
            self.send(f"⚠️ Не удалось выполнить команду: <code>{html.escape(type(exc).__name__)}</code>.")

    def run(self) -> int:
        if not legacy.BOT_TOKEN or not legacy.BOT_CHAT_ID or not legacy.GITHUB_TOKEN or not legacy.GITHUB_REPOSITORY:
            raise RuntimeError("BOT_TOKEN, BOT_CHAT_ID, GITHUB_TOKEN and GITHUB_REPOSITORY are required")
        self.load_access(force=True)
        self.setup_bot()
        refresh_thread = threading.Thread(target=self.refresh_loop, name="snapshot-refresh", daemon=True)
        refresh_thread.start()
        print(f"Telegram panel v2 started for {legacy.GITHUB_REPOSITORY}; run_seconds={legacy.RUN_SECONDS}")
        deadline = time.monotonic() + legacy.RUN_SECONDS
        try:
            while time.monotonic() < deadline:
                try:
                    payload: dict[str, Any] = {
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query", "my_chat_member"],
                    }
                    if self.offset is not None:
                        payload["offset"] = self.offset
                    response = self.telegram_api("getUpdates", payload)
                    for update in response.get("result", []):
                        if not isinstance(update, dict):
                            continue
                        update_id = int(update.get("update_id", 0))
                        self.offset = max(self.offset or 0, update_id + 1)
                        try:
                            self.handle_update(update)
                        except Exception as exc:
                            print(f"ERROR update {update_id}: {type(exc).__name__}: {exc}")
                except requests.RequestException as exc:
                    print(f"WARNING polling network error: {type(exc).__name__}: {exc}")
                    time.sleep(3)
                except Exception as exc:
                    print(f"WARNING polling error: {type(exc).__name__}: {exc}")
                    time.sleep(2)
        finally:
            self.stop_refresh.set()
            self.refresh_requested.set()
        print("Telegram panel v2 shift completed normally.")
        return 0


def self_test() -> None:
    bot = TelegramPanelV2()
    access = bot.normalize_access({"owner_id": 123, "admins": [456, "456"], "settings": {"public_panel": False}})
    assert access["owner_id"] == "123"
    assert access["admins"] == ["456"]
    assert access["settings"]["public_panel"] is False
    assert access["settings"]["notifications"] is True
    assert bot.source_mode_name("primary") == "Основные источники"
    assert bot.source_status_name("quarantined") == "временно приостановлен"
    assert POST_URL_RE.search("https://telegram.me/testchan/123")
    assert WHEEL_LINK_RE.search("https://betboom.ru/freestream/test")
    print("admin_panel_v2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelV2().run()


if __name__ == "__main__":
    raise SystemExit(main())
