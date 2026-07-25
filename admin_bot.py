from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests


UTC = timezone.utc
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Barnaul"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_CHAT_ID = os.getenv("BOT_CHAT_ID", "").strip()
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv(
    "GITHUB_REPOSITORY", "slavagostev2-dot/betboom-wheel-monitor"
).strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
RUN_SECONDS = max(60, int(os.getenv("RUN_SECONDS", "19800")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")))
CACHE_SECONDS = max(5, int(os.getenv("ADMIN_CACHE_SECONDS", "15")))
MONITOR_INTERVAL_MINUTES = max(1, int(os.getenv("MONITOR_INTERVAL_MINUTES", "5")))
SOURCE_INACTIVITY_DAYS = max(1, int(os.getenv("SOURCE_INACTIVITY_DAYS", "7")))
SOURCE_INACTIVITY_REPORT_DAYS = max(
    1, int(os.getenv("SOURCE_INACTIVITY_REPORT_DAYS", "7"))
)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
GH_API = "https://api.github.com"
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

BTN_STATS = "📊 Статистика"
BTN_ACTIVE = "🔥 Активные колёса"
BTN_SOURCES = "📡 Источники"
BTN_RANKING = "🏆 Рейтинг каналов"
BTN_REPORTS = "📅 Отчёты"
BTN_DISCOVERY = "🔎 Поиск новых"
BTN_SETTINGS = "⚙️ Настройки"
BTN_CONTROL = "🛠 Управление"
BTN_MENU = "🏠 Главное меню"

MAIN_KEYBOARD = {
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

COMMANDS = [
    {"command": "start", "description": "Открыть админ-панель"},
    {"command": "menu", "description": "Главное меню"},
    {"command": "status", "description": "Состояние монитора"},
    {"command": "stats", "description": "Статистика"},
    {"command": "active", "description": "Активные колёса"},
    {"command": "sources", "description": "Источники"},
    {"command": "ranking", "description": "Рейтинг каналов"},
    {"command": "reports", "description": "Отчёты"},
]


@dataclass
class Snapshot:
    state: dict[str, Any]
    stats: dict[str, Any]
    health: dict[str, Any]
    discovery: dict[str, Any]
    unknown: dict[str, Any]
    fast: list[str]
    nightly: list[str]


class AdminBot:
    def __init__(self) -> None:
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "betboom-wheel-admin/1.0"})
        self.offset: int | None = None
        self.cache: tuple[float, Snapshot] | None = None
        self.pending_input: dict[int, dict[str, Any]] = {}

    def telegram_api(self, method: str, payload: dict[str, Any] | None = None) -> dict:
        response = self.http.post(
            f"{TG_API}/{method}", json=payload or {}, timeout=REQUEST_TIMEOUT + 30
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data

    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id or BOT_CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.telegram_api("sendMessage", payload)

    def answer(self, query_id: str, text: str = "Готово") -> None:
        if not query_id:
            return
        try:
            self.telegram_api(
                "answerCallbackQuery",
                {"callback_query_id": query_id, "text": text[:180]},
            )
        except Exception as exc:
            print(f"WARNING callback answer: {type(exc).__name__}: {exc}")

    def setup_bot(self) -> None:
        self.telegram_api("deleteWebhook", {"drop_pending_updates": False})
        self.telegram_api("setMyCommands", {"commands": COMMANDS})
        self.telegram_api(
            "setChatMenuButton",
            {"chat_id": BOT_CHAT_ID, "menu_button": {"type": "commands"}},
        )

    def gh_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def gh_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> requests.Response:
        response = self.http.request(
            method,
            f"{GH_API}{path}",
            headers=self.gh_headers(),
            json=json_body,
            timeout=REQUEST_TIMEOUT + 15,
        )
        if response.status_code not in expected:
            raise RuntimeError(
                f"GitHub API {method} {path}: {response.status_code} {response.text[:300]}"
            )
        return response

    def get_file(
        self,
        path: str,
        *,
        branch: str | None = None,
    ) -> tuple[str, str]:
        encoded_path = quote(path, safe="/")
        target_branch = str(branch or GITHUB_BRANCH)
        response = self.gh_request(
            "GET",
            f"/repos/{GITHUB_REPOSITORY}/contents/{encoded_path}?ref={quote(target_branch)}",
        )
        data = response.json()
        content = base64.b64decode(data.get("content", "")).decode("utf-8")
        return content, str(data.get("sha") or "")

    def get_json_file(self, path: str, default: dict[str, Any]) -> dict[str, Any]:
        try:
            text, _ = self.get_file(path)
            value = json.loads(text)
        except Exception as exc:
            print(f"WARNING read {path}: {type(exc).__name__}: {exc}")
            return default
        return value if isinstance(value, dict) else default

    def update_file(
        self,
        path: str,
        content: str,
        message: str,
        *,
        branch: str | None = None,
    ) -> None:
        target_branch = str(branch or GITHUB_BRANCH)
        _, sha = self.get_file(path, branch=target_branch)
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": target_branch,
        }
        self.gh_request(
            "PUT",
            f"/repos/{GITHUB_REPOSITORY}/contents/{quote(path, safe='/')}",
            json_body=body,
            expected=(200, 201),
        )
        self.cache = None

    def dispatch(self, workflow: str, inputs: dict[str, str] | None = None) -> None:
        body: dict[str, Any] = {"ref": GITHUB_BRANCH}
        if inputs:
            body["inputs"] = inputs
        self.gh_request(
            "POST",
            f"/repos/{GITHUB_REPOSITORY}/actions/workflows/{quote(workflow)}/dispatches",
            json_body=body,
            expected=(204,),
        )

    def workflow_run(self, workflow: str) -> dict[str, Any]:
        response = self.gh_request(
            "GET",
            f"/repos/{GITHUB_REPOSITORY}/actions/workflows/{quote(workflow)}/runs?per_page=1",
        )
        runs = response.json().get("workflow_runs", [])
        return runs[0] if runs else {}

    @staticmethod
    def parse_list(text: str) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in text.splitlines():
            value = raw.split("#", 1)[0].strip().lstrip("@")
            if not value or value.casefold() in seen:
                continue
            seen.add(value.casefold())
            result.append(value)
        return result

    def snapshot(self, *, force: bool = False) -> Snapshot:
        now = time.monotonic()
        if not force and self.cache and now - self.cache[0] < CACHE_SECONDS:
            return self.cache[1]
        state = self.get_json_file("state.json", {})
        stats = self.get_json_file("source_stats.json", {"sources": {}, "daily": {}})
        health = self.get_json_file("source_health.json", {"sources": {}})
        discovery = self.get_json_file("discovery_state.json", {})
        unknown = self.get_json_file("unknown_timer_samples.json", {"samples": []})
        fast_text, _ = self.get_file("public_sources.txt")
        nightly_text, _ = self.get_file("source_catalog.txt")
        value = Snapshot(
            state=state,
            stats=stats,
            health=health,
            discovery=discovery,
            unknown=unknown,
            fast=self.parse_list(fast_text),
            nightly=self.parse_list(nightly_text),
        )
        self.cache = (now, value)
        return value

    @staticmethod
    def parse_dt(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return result if result.tzinfo else result.replace(tzinfo=UTC)

    @classmethod
    def fmt_dt(cls, value: Any) -> str:
        dt = cls.parse_dt(value)
        return dt.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M") if dt else "нет данных"

    @classmethod
    def age_text(cls, value: Any) -> str:
        dt = cls.parse_dt(value)
        if not dt:
            return "нет данных"
        seconds = max(0, int((datetime.now(UTC) - dt.astimezone(UTC)).total_seconds()))
        if seconds < 60:
            return f"{seconds} сек. назад"
        if seconds < 3600:
            return f"{seconds // 60} мин. назад"
        if seconds < 86400:
            return f"{seconds // 3600} ч. назад"
        return f"{seconds // 86400} дн. назад"

    @staticmethod
    def counter(entry: Any, name: str) -> int:
        return int(entry.get(name, 0)) if isinstance(entry, dict) else 0

    @staticmethod
    def safe_source(value: str) -> str:
        return value.strip().lstrip("@").split("/", 1)[0]

    def period_totals(self, stats: dict[str, Any], days: int) -> dict[str, int]:
        result: dict[str, int] = {}
        today = datetime.now(DISPLAY_TZ).date()
        allowed = {(today - timedelta(days=i)).isoformat() for i in range(days)}
        for day, entry in stats.get("daily", {}).items():
            if day not in allowed or not isinstance(entry, dict):
                continue
            for name, value in entry.get("totals", {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    result[name] = result.get(name, 0) + int(value)
        return result

    def merged_source_stats(self, snap: Snapshot) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {
            name: dict(entry)
            for name, entry in snap.stats.get("sources", {}).items()
            if isinstance(entry, dict)
        }
        for name, entry in snap.discovery.get("stats_sources", {}).items():
            if not isinstance(entry, dict):
                continue
            target = result.setdefault(name, {})
            for key, value in entry.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    target[key] = int(target.get(key, 0)) + int(value)
                elif key.startswith("last_") and str(value) > str(target.get(key, "")):
                    target[key] = value
        return result

    def show_menu(self) -> None:
        snap = self.snapshot()
        state = snap.state
        text = (
            "🎡 <b>BetBoom Monitor — админ-панель</b>\n\n"
            f"Монитор: <b>{self.monitor_state_text()}</b>\n"
            f"Последний heartbeat: {self.age_text(state.get('last_heartbeat_at'))}\n"
            f"Быстрых источников: {len(snap.fast)}\n"
            f"Активных колёс: {len(state.get('active_wheels', {}))}\n\n"
            "Все основные разделы доступны на постоянной клавиатуре ниже."
        )
        self.send(text, reply_markup=MAIN_KEYBOARD)

    def monitor_state_text(self) -> str:
        try:
            run = self.workflow_run("monitor.yml")
        except Exception:
            return "статус GitHub недоступен"
        status = str(run.get("status") or "unknown")
        conclusion = str(run.get("conclusion") or "")
        if status in {"queued", "in_progress", "waiting", "pending"}:
            return "🟢 работает"
        if conclusion == "success":
            return "🟡 последняя смена завершена"
        if conclusion:
            return f"🔴 {html.escape(conclusion)}"
        return html.escape(status)

    def show_status(self) -> None:
        snap = self.snapshot(force=True)
        state = snap.state
        health_entries = [
            entry for entry in snap.health.get("sources", {}).values() if isinstance(entry, dict)
        ]
        ok = sum(1 for entry in health_entries if entry.get("status") == "ok")
        quarantined = sum(
            1 for entry in health_entries if entry.get("status") == "quarantined"
        )
        problems = len(health_entries) - ok - quarantined
        summary = state.get("last_run_summary", {})
        run = self.workflow_run("monitor.yml")
        run_url = str(run.get("html_url") or "")
        run_line = (
            f'<a href="{html.escape(run_url, quote=True)}">Открыть последний запуск</a>'
            if run_url
            else "Ссылка на запуск недоступна"
        )
        text = (
            "🩺 <b>Состояние системы</b>\n\n"
            f"GitHub workflow: <b>{self.monitor_state_text()}</b>\n"
            f"Heartbeat: {self.fmt_dt(state.get('last_heartbeat_at'))} "
            f"({self.age_text(state.get('last_heartbeat_at'))})\n"
            f"Последний тип запуска: {html.escape(str(state.get('last_run_kind') or 'нет'))}\n\n"
            f"Источники: {len(snap.fast)} FAST + {len(snap.nightly)} NIGHTLY\n"
            f"Доступны: {ok}\n"
            f"В карантине: {quarantined}\n"
            f"С проблемами: {problems}\n"
            f"Последняя проверка охватила: {self.counter(summary, 'checked_sources')}\n"
            f"Ошибок в последнем цикле: {self.counter(summary, 'source_errors')}\n\n"
            f"{run_line}"
        )
        self.send(
            text,
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🔄 Обновить", "callback_data": "page:status"}],
                    [{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}],
                ]
            },
        )

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot(force=True)
        totals = self.period_totals(snap.stats, days)
        state = snap.state
        title = "Сегодня" if days == 1 else f"За {days} дней"
        text = (
            f"📊 <b>Статистика — {title}</b>\n\n"
            f"Проверок: {totals.get('checks', 0)}\n"
            f"Просмотрено сообщений: {totals.get('messages_scanned', 0)}\n"
            f"Новых постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Предварительных уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Подтверждённых активаций: {totals.get('activation_sent', 0)}\n"
            f"Повторов подавлено: {totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок источников: {totals.get('errors', 0)}\n"
            f"Неизвестных таймеров сохранено: {totals.get('unknown_timer_samples', 0)}\n\n"
            f"Сейчас активных колёс: {len(state.get('active_wheels', {}))}\n"
            f"Ожидают подтверждения: {len(state.get('pending_posts', {}))}\n"
            f"Источников FAST: {len(snap.fast)}\n"
            f"Источников NIGHTLY: {len(snap.nightly)}"
        )
        self.send(
            text,
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Сегодня", "callback_data": "stats:1"},
                        {"text": "7 дней", "callback_data": "stats:7"},
                        {"text": "30 дней", "callback_data": "stats:30"},
                    ],
                    [{"text": "🏆 Рейтинг", "callback_data": "page:ranking"}],
                ]
            },
        )

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
        snap = self.snapshot(force=True)
        rows = self.active_rows(snap)
        if not rows:
            self.send(
                "🔥 <b>Активных колёс сейчас нет.</b>",
                reply_markup={
                    "inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "page:active"}]]
                },
            )
            return
        lines = [f"🔥 <b>Активные колёса: {len(rows)}</b>", ""]
        keyboard: list[list[dict[str, str]]] = []
        participating = snap.state.get("participating_wheels", {})
        participating_keys = {str(x).casefold() for x in participating}
        for index, (key, entry) in enumerate(rows[:20], 1):
            identifier = str(entry.get("identifier") or key)
            source = str(entry.get("source") or "неизвестно")
            deadline = self.parse_dt(entry.get("deadline"))
            timing = self.remaining(deadline) if deadline else "время не найдено"
            part = "✅" if key.casefold() in participating_keys else "❌"
            lines.append(
                f"{index}. <code>{html.escape(identifier)}</code> — {html.escape(timing)} — {part}\n"
                f"   @{html.escape(source)}"
            )
            row: list[dict[str, str]] = []
            if entry.get("url"):
                row.append({"text": f"🎡 {identifier[:18]}", "url": str(entry["url"])})
            if key.casefold() not in participating_keys:
                row.append({"text": "✅ Участвую", "callback_data": f"wheel:part:{key}"})
            if row:
                keyboard.append(row)
            keyboard.append(
                [
                    {"text": "🔄 Проверить", "callback_data": f"wheel:check:{key}"},
                    {"text": "🗑 Убрать", "callback_data": f"wheel:removeask:{key}"},
                ]
            )
        keyboard.append([{"text": "🔄 Обновить список", "callback_data": "page:active"}])
        self.send("\n".join(lines), reply_markup={"inline_keyboard": keyboard})

    @staticmethod
    def remaining(deadline: datetime | None) -> str:
        if not deadline:
            return "не определено"
        delta = deadline.astimezone(UTC) - datetime.now(UTC)
        seconds = int(delta.total_seconds())
        if seconds <= 0:
            return "время вышло"
        hours, rem = divmod(seconds, 3600)
        minutes = rem // 60
        if hours:
            return f"{hours} ч. {minutes} мин."
        return f"{max(1, minutes)} мин."

    def source_sets(self, snap: Snapshot) -> dict[str, list[str]]:
        health = snap.health.get("sources", {})
        quarantined = sorted(
            [
                name
                for name, entry in health.items()
                if isinstance(entry, dict) and entry.get("status") == "quarantined"
            ],
            key=str.casefold,
        )
        inactive: list[str] = []
        now = datetime.now(UTC)
        stats_sources = snap.stats.get("sources", {})
        for source in snap.fast:
            entry = stats_sources.get(source, {})
            if not isinstance(entry, dict):
                continue
            reference = self.parse_dt(entry.get("last_wheel_post_at")) or self.parse_dt(
                entry.get("first_checked_at")
            )
            if reference and now - reference >= timedelta(days=SOURCE_INACTIVITY_DAYS):
                inactive.append(source)
        return {
            "fast": snap.fast,
            "nightly": snap.nightly,
            "quarantine": quarantined,
            "inactive": sorted(inactive, key=str.casefold),
        }

    def show_sources(self) -> None:
        snap = self.snapshot(force=True)
        groups = self.source_sets(snap)
        text = (
            "📡 <b>Источники</b>\n\n"
            f"FAST: {len(groups['fast'])}\n"
            f"NIGHTLY: {len(groups['nightly'])}\n"
            f"В карантине: {len(groups['quarantine'])}\n"
            f"Без колёс {SOURCE_INACTIVITY_DAYS}+ дней: {len(groups['inactive'])}\n\n"
            "Выберите список или добавьте источник."
        )
        self.send(
            text,
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": f"⚡ FAST ({len(groups['fast'])})", "callback_data": "sl:fast:0"},
                        {"text": f"🌙 NIGHTLY ({len(groups['nightly'])})", "callback_data": "sl:nightly:0"},
                    ],
                    [
                        {"text": f"🟡 Карантин ({len(groups['quarantine'])})", "callback_data": "sl:quarantine:0"},
                        {"text": f"📭 7+ дней ({len(groups['inactive'])})", "callback_data": "sl:inactive:0"},
                    ],
                    [{"text": "➕ Добавить источник", "callback_data": "source:add"}],
                ]
            },
        )

    def show_source_list(self, group: str, page: int = 0) -> None:
        snap = self.snapshot(force=True)
        groups = self.source_sets(snap)
        rows = groups.get(group, [])
        per_page = 10
        page = max(0, min(page, max(0, (len(rows) - 1) // per_page)))
        part = rows[page * per_page : (page + 1) * per_page]
        title = {
            "fast": "FAST",
            "nightly": "NIGHTLY",
            "quarantine": "Карантин",
            "inactive": f"Без колёс {SOURCE_INACTIVITY_DAYS}+ дней",
        }.get(group, group)
        lines = [f"📡 <b>{html.escape(title)}</b>", ""]
        keyboard: list[list[dict[str, str]]] = []
        stats_sources = snap.stats.get("sources", {})
        for source in part:
            entry = stats_sources.get(source, {})
            wheels = self.counter(entry, "wheel_posts")
            lines.append(f"• @{html.escape(source)} — колёс: {wheels}")
            keyboard.append(
                [{"text": f"@{source}", "callback_data": f"sd:{source}"}]
            )
        if not part:
            lines.append("Список пуст.")
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "◀️", "callback_data": f"sl:{group}:{page - 1}"})
        if (page + 1) * per_page < len(rows):
            nav.append({"text": "▶️", "callback_data": f"sl:{group}:{page + 1}"})
        if nav:
            keyboard.append(nav)
        keyboard.append([{"text": "↩️ Источники", "callback_data": "page:sources"}])
        self.send("\n".join(lines), reply_markup={"inline_keyboard": keyboard})

    def show_source_detail(self, source: str) -> None:
        source = self.safe_source(source)
        snap = self.snapshot(force=True)
        stats = self.merged_source_stats(snap).get(source, {})
        health = snap.health.get("sources", {}).get(source, {})
        discovery = snap.discovery.get("sources", {}).get(source, {})
        mode = "FAST" if source.casefold() in {x.casefold() for x in snap.fast} else (
            "NIGHTLY" if source.casefold() in {x.casefold() for x in snap.nightly} else "не настроен"
        )
        status = str(health.get("status") or discovery.get("status") or "нет данных")
        wheels = self.counter(stats, "wheel_posts") or self.counter(discovery, "wheel_links_found")
        score = int(stats.get("quality_score", 0) or 0)
        text = (
            f"📡 <b>@{html.escape(source)}</b>\n\n"
            f"Режим: <b>{html.escape(mode)}</b>\n"
            f"Статус: {html.escape(status)}\n"
            f"Проверок: {self.counter(stats, 'checks')}\n"
            f"Постов с колёсами: {wheels}\n"
            f"Очки рейтинга: {score}\n"
            f"Последнее колесо: {self.fmt_dt(stats.get('last_wheel_post_at') or discovery.get('latest_wheel_at'))}\n"
            f"Последняя проверка: {self.fmt_dt(health.get('last_checked_at') or discovery.get('checked_at'))}\n"
        )
        keyboard: list[list[dict[str, str]]] = [
            [{"text": "Открыть Telegram", "url": f"https://telegram.me/{source}"}],
        ]
        move_row: list[dict[str, str]] = []
        if mode != "FAST":
            move_row.append({"text": "⚡ В FAST", "callback_data": f"source:move:fast:{source}"})
        if mode != "NIGHTLY":
            move_row.append({"text": "🌙 В NIGHTLY", "callback_data": f"source:move:nightly:{source}"})
        if move_row:
            keyboard.append(move_row)
        if status == "quarantined":
            keyboard.append(
                [{"text": "🟢 Снять карантин", "callback_data": f"source:clearq:{source}"}]
            )
        keyboard.append(
            [{"text": "🗑 Удалить", "callback_data": f"source:removeask:{source}"}]
        )
        keyboard.append([{"text": "↩️ Источники", "callback_data": "page:sources"}])
        self.send(text, reply_markup={"inline_keyboard": keyboard})

    def show_ranking(self) -> None:
        snap = self.snapshot(force=True)
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
        self.send(
            "\n".join(lines),
            reply_markup={
                "inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "page:ranking"}]]
            },
        )

    def show_reports(self) -> None:
        self.send(
            "📅 <b>Отчёты</b>\n\nВыберите период или специальный отчёт.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Сегодня", "callback_data": "report:1"},
                        {"text": "7 дней", "callback_data": "report:7"},
                        {"text": "30 дней", "callback_data": "report:30"},
                    ],
                    [{"text": "📭 Неактивные каналы", "callback_data": "report:inactive"}],
                    [{"text": "⚠️ Ошибки и карантин", "callback_data": "report:errors"}],
                    [{"text": "📨 Отправить ежедневный отчёт", "callback_data": "control:daily"}],
                ]
            },
        )

    def show_period_report(self, days: int) -> None:
        snap = self.snapshot(force=True)
        totals = self.period_totals(snap.stats, days)
        today = datetime.now(DISPLAY_TZ).date()
        allowed = {(today - timedelta(days=i)).isoformat() for i in range(days)}
        merged: dict[str, int] = {}
        for day, entry in snap.stats.get("daily", {}).items():
            if day not in allowed or not isinstance(entry, dict):
                continue
            for source, source_entry in entry.get("sources", {}).items():
                merged[source] = merged.get(source, 0) + self.counter(source_entry, "wheel_posts")
        source_rows = sorted(merged.items(), key=lambda item: (-item[1], item[0].casefold()))
        top = [f"• @{html.escape(source)} — {count}" for source, count in source_rows[:5] if count]
        if not top:
            top = ["• данных пока нет"]
        text = (
            f"📅 <b>Отчёт за {days} дн.</b>\n\n"
            f"Проверок: {totals.get('checks', 0)}\n"
            f"Постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Активаций: {totals.get('activation_sent', 0)}\n"
            f"Повторов подавлено: {totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок: {totals.get('errors', 0)}\n\n"
            "<b>Лучшие источники периода</b>\n" + "\n".join(top)
        )
        self.send(text)

    def show_inactive_report(self) -> None:
        snap = self.snapshot(force=True)
        rows = self.source_sets(snap)["inactive"]
        stats = snap.stats.get("sources", {})
        lines = [f"📭 <b>Без колёс {SOURCE_INACTIVITY_DAYS}+ дней: {len(rows)}</b>", ""]
        for source in rows[:40]:
            entry = stats.get(source, {})
            last = entry.get("last_wheel_post_at") or entry.get("first_checked_at")
            lines.append(f"• @{html.escape(source)} — {self.fmt_dt(last)}")
        if not rows:
            lines.append("Таких каналов сейчас нет.")
        self.send("\n".join(lines))

    def show_errors_report(self) -> None:
        snap = self.snapshot(force=True)
        rows: list[tuple[str, dict[str, Any]]] = []
        for source, entry in snap.health.get("sources", {}).items():
            if isinstance(entry, dict) and entry.get("status") != "ok":
                rows.append((source, entry))
        rows.sort(key=lambda item: (item[1].get("status") != "quarantined", item[0].casefold()))
        lines = [f"⚠️ <b>Проблемные источники: {len(rows)}</b>", ""]
        for source, entry in rows[:35]:
            reason = str(entry.get("last_error") or entry.get("status") or "ошибка")
            lines.append(f"• @{html.escape(source)} — {html.escape(reason[:90])}")
        if not rows:
            lines.append("Проблемных источников нет.")
        self.send("\n".join(lines))

    def show_discovery(self) -> None:
        snap = self.snapshot(force=True)
        discovery_sources = snap.discovery.get("sources", {})
        candidates: list[tuple[str, int, Any]] = []
        fast_set = {x.casefold() for x in snap.fast}
        for source, entry in discovery_sources.items():
            if not isinstance(entry, dict) or source.casefold() in fast_set:
                continue
            found = self.counter(entry, "wheel_links_found")
            candidates.append((source, found, entry.get("latest_wheel_at")))
        candidates.sort(key=lambda item: (-item[1], str(item[2] or ""), item[0].casefold()))
        lines = ["🔎 <b>Поиск новых источников</b>", ""]
        lines.append(f"Последний ночной запуск: {self.fmt_dt(snap.discovery.get('last_run_at'))}")
        lines.append(f"Источников в ночном каталоге: {len(snap.nightly)}")
        lines.append(f"Ошибок последнего поиска: {self.counter(snap.discovery, 'error_count')}")
        lines.append("")
        lines.append("<b>Кандидаты с найденными колёсами</b>")
        shown = 0
        for source, found, latest in candidates:
            if found <= 0:
                continue
            shown += 1
            lines.append(
                f"• @{html.escape(source)} — ссылок {found}, последнее {self.fmt_dt(latest)}"
            )
            if shown >= 10:
                break
        if not shown:
            lines.append("• новых сильных кандидатов пока нет")
        self.send(
            "\n".join(lines),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🌙 Запустить ночной поиск", "callback_data": "control:nightly"}],
                    [{"text": "📡 NIGHTLY-источники", "callback_data": "sl:nightly:0"}],
                ]
            },
        )

    def show_settings(self) -> None:
        text = (
            "⚙️ <b>Текущие настройки</b>\n\n"
            f"Быстрая проверка: каждые {MONITOR_INTERVAL_MINUTES} минут\n"
            f"Отчёт неактивных каналов: через {SOURCE_INACTIVITY_DAYS} дней\n"
            f"Период отчёта: раз в {SOURCE_INACTIVITY_REPORT_DAYS} дней\n"
            "Уведомления о колёсах: включены\n"
            "Добавление источников: только вручную администратором\n"
            "Автоматическое удаление источников: выключено\n\n"
            "Здесь показаны только реально действующие параметры. Изменение интервалов "
            "добавим отдельно, когда понадобится."
        )
        self.send(text)

    def show_control(self) -> None:
        self.send(
            "🛠 <b>Управление</b>\n\nВсе команды запускают реальные GitHub Actions.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "▶️ Проверить сейчас", "callback_data": "control:monitor"}],
                    [{"text": "🌙 Ночной поиск", "callback_data": "control:nightly"}],
                    [{"text": "📨 Ежедневный отчёт", "callback_data": "control:daily"}],
                    [{"text": "🩺 Состояние системы", "callback_data": "page:status"}],
                ]
            },
        )

    @staticmethod
    def remove_from_list_text(text: str, username: str) -> str:
        key = username.casefold()
        lines = [
            line
            for line in text.splitlines()
            if line.split("#", 1)[0].strip().lstrip("@").casefold() != key
        ]
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def append_to_list_text(text: str, username: str) -> str:
        existing = {x.casefold() for x in AdminBot.parse_list(text)}
        if username.casefold() in existing:
            return text if text.endswith("\n") else text + "\n"
        return text.rstrip() + f"\n{username}\n"

    def set_source_mode(self, username: str, mode: str) -> str:
        username = self.safe_source(username)
        if not USERNAME_RE.fullmatch(username):
            raise ValueError("Некорректный username Telegram")
        fast_text, _ = self.get_file("public_sources.txt")
        nightly_text, _ = self.get_file("source_catalog.txt")
        fast_new = self.remove_from_list_text(fast_text, username)
        nightly_new = self.remove_from_list_text(nightly_text, username)
        if mode == "fast":
            fast_new = self.append_to_list_text(fast_new, username)
        elif mode == "nightly":
            nightly_new = self.append_to_list_text(nightly_new, username)
        elif mode != "remove":
            raise ValueError("Неизвестный режим")
        if fast_new != fast_text:
            self.update_file(
                "public_sources.txt",
                fast_new,
                f"Set @{username} source mode via Telegram admin",
            )
        if nightly_new != nightly_text:
            self.update_file(
                "source_catalog.txt",
                nightly_new,
                f"Set @{username} source mode via Telegram admin",
            )
        self.cache = None
        if mode == "remove":
            return f"@{username} удалён из мониторинга."
        return f"@{username} установлен в режим {mode.upper()}."

    def verify_public_source(self, username: str) -> tuple[bool, str]:
        try:
            response = self.http.get(
                f"https://telegram.me/s/{username}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            return False, f"не удалось проверить: {type(exc).__name__}"
        if response.status_code != 200:
            return False, f"Telegram вернул HTTP {response.status_code}"
        lowered = response.text.casefold()
        if "tgme_channel_info" not in lowered and "tgme_widget_message" not in lowered:
            return False, "публичные сообщения не найдены"
        return True, "публичный источник доступен"

    def authorized(self, chat_id: Any, user_id: Any) -> bool:
        if str(chat_id) != BOT_CHAT_ID:
            return False
        return not ADMIN_USER_ID or str(user_id) == ADMIN_USER_ID

    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = message.get("from") if isinstance(message, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        user_id = sender.get("id") if isinstance(sender, dict) else None
        if not self.authorized(chat_id, user_id):
            return
        text = str(message.get("text") or "").strip()
        if not text:
            return
        command = text.split("@", 1)[0].split(maxsplit=1)[0].casefold()
        if command in {"/start", "/menu"} or text == BTN_MENU:
            self.pending_input.pop(int(user_id), None)
            self.show_menu()
            return
        pending = self.pending_input.get(int(user_id))
        if pending and pending.get("kind") == "add_source":
            username = self.safe_source(text)
            if not USERNAME_RE.fullmatch(username):
                self.send(
                    "Username некорректен. Отправьте публичный username без ссылки, например: "
                    "<code>gazazor</code>."
                )
                return
            available, detail = self.verify_public_source(username)
            if not available:
                self.send(
                    f"⚠️ @{html.escape(username)} не добавлен: {html.escape(detail)}.\n"
                    "Проверьте username или отправьте другой."
                )
                return
            self.pending_input[int(user_id)] = {"kind": "choose_mode", "source": username}
            self.send(
                f"✅ @{html.escape(username)}: {html.escape(detail)}.\n\n"
                "Выберите режим проверки:",
                reply_markup={
                    "inline_keyboard": [
                        [
                            {"text": "⚡ FAST", "callback_data": f"source:addmode:fast:{username}"},
                            {"text": "🌙 NIGHTLY", "callback_data": f"source:addmode:nightly:{username}"},
                        ],
                        [{"text": "Отмена", "callback_data": "source:addcancel"}],
                    ]
                },
            )
            return
        if text == BTN_STATS or command == "/stats":
            self.show_stats()
        elif text == BTN_ACTIVE or command in {"/active", "/wheels"}:
            self.show_active()
        elif text == BTN_SOURCES or command == "/sources":
            self.show_sources()
        elif text == BTN_RANKING or command == "/ranking":
            self.show_ranking()
        elif text == BTN_REPORTS or command == "/reports":
            self.show_reports()
        elif text == BTN_DISCOVERY:
            self.show_discovery()
        elif text == BTN_SETTINGS:
            self.show_settings()
        elif text == BTN_CONTROL:
            self.show_control()
        elif command == "/status":
            self.show_status()
        else:
            self.send("Команда не распознана. Используйте постоянное меню.", reply_markup=MAIN_KEYBOARD)

    def dispatch_admin_action(self, action: str, value: str) -> None:
        self.dispatch("admin-action.yml", {"action": action, "value": value})

    def handle_callback(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = query.get("from") if isinstance(query, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        user_id = sender.get("id") if isinstance(sender, dict) else None
        if not self.authorized(chat_id, user_id):
            self.answer(query_id, "Недоступно")
            return
        data = str(query.get("data") or "")
        try:
            if data == "page:status":
                self.answer(query_id, "Обновляю")
                self.show_status()
            elif data == "page:active" or data == "bb:l:active":
                self.answer(query_id, "Обновляю")
                self.show_active()
            elif data == "page:sources":
                self.answer(query_id, "Обновляю")
                self.show_sources()
            elif data == "page:ranking":
                self.answer(query_id, "Обновляю")
                self.show_ranking()
            elif data.startswith("stats:"):
                self.answer(query_id, "Готово")
                self.show_stats(int(data.split(":", 1)[1]))
            elif data.startswith("report:"):
                value = data.split(":", 1)[1]
                self.answer(query_id, "Формирую")
                if value.isdigit():
                    self.show_period_report(int(value))
                elif value == "inactive":
                    self.show_inactive_report()
                elif value == "errors":
                    self.show_errors_report()
            elif data.startswith("sl:"):
                _, group, page = data.split(":", 2)
                self.answer(query_id, "Открываю")
                self.show_source_list(group, int(page))
            elif data.startswith("sd:"):
                self.answer(query_id, "Открываю")
                self.show_source_detail(data.split(":", 1)[1])
            elif data == "source:add":
                self.pending_input[int(user_id)] = {"kind": "add_source"}
                self.answer(query_id, "Жду username")
                self.send(
                    "➕ Отправьте публичный username Telegram-канала или чата без ссылки.\n\n"
                    "Пример: <code>gazazor</code>"
                )
            elif data == "source:addcancel":
                self.pending_input.pop(int(user_id), None)
                self.answer(query_id, "Отменено")
                self.show_sources()
            elif data.startswith("source:addmode:"):
                _, _, mode, source = data.split(":", 3)
                result = self.set_source_mode(source, mode)
                self.pending_input.pop(int(user_id), None)
                self.answer(query_id, "Добавлено")
                self.send(f"✅ {html.escape(result)}", reply_markup=MAIN_KEYBOARD)
            elif data.startswith("source:move:"):
                _, _, mode, source = data.split(":", 3)
                result = self.set_source_mode(source, mode)
                self.answer(query_id, "Изменено")
                self.send(f"✅ {html.escape(result)}")
            elif data.startswith("source:removeask:"):
                source = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Удалить @{html.escape(source)} из FAST и NIGHTLY?",
                    reply_markup={
                        "inline_keyboard": [
                            [
                                {"text": "Да, удалить", "callback_data": f"source:remove:{source}"},
                                {"text": "Отмена", "callback_data": f"sd:{source}"},
                            ]
                        ]
                    },
                )
            elif data.startswith("source:remove:"):
                source = data.split(":", 2)[2]
                result = self.set_source_mode(source, "remove")
                self.answer(query_id, "Удалено")
                self.send(f"✅ {html.escape(result)}")
            elif data.startswith("source:clearq:"):
                source = data.split(":", 2)[2]
                self.dispatch_admin_action("clear_quarantine", source)
                self.answer(query_id, "Поставлено в очередь")
                self.send(f"🟢 Снятие карантина @{html.escape(source)} запущено.")
            elif data.startswith("control:"):
                action = data.split(":", 1)[1]
                workflows = {
                    "monitor": ("monitor.yml", {"continuous": "true"}, "Проверка запущена"),
                    "nightly": ("nightly-discovery.yml", None, "Ночной поиск запущен"),
                    "daily": ("daily-report.yml", None, "Ежедневный отчёт запущен"),
                }
                workflow, inputs, answer = workflows[action]
                self.dispatch(workflow, inputs)
                self.answer(query_id, answer)
                self.send(f"▶️ {html.escape(answer)}.")
            elif data.startswith("bb:p:"):
                token = data.split(":", 2)[2]
                self.dispatch_admin_action("participate_token", token)
                self.answer(query_id, "Участие отмечается")
            elif data.startswith("bb:n:"):
                self.answer(query_id, "Участие уже отмечено")
            elif data.startswith("wheel:part:"):
                key = data.split(":", 2)[2]
                self.dispatch_admin_action("participate_wheel", key)
                self.answer(query_id, "Участие отмечается")
            elif data.startswith("wheel:check:"):
                key = data.split(":", 2)[2]
                self.dispatch_admin_action("recheck_wheel", key)
                self.answer(query_id, "Проверка запущена")
            elif data.startswith("wheel:removeask:"):
                key = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Убрать колесо <code>{html.escape(key)}</code> из активного списка?",
                    reply_markup={
                        "inline_keyboard": [
                            [
                                {"text": "Да, убрать", "callback_data": f"wheel:remove:{key}"},
                                {"text": "Отмена", "callback_data": "page:active"},
                            ]
                        ]
                    },
                )
            elif data.startswith("wheel:remove:"):
                key = data.split(":", 2)[2]
                self.dispatch_admin_action("remove_active", key)
                self.answer(query_id, "Удаление запущено")
            elif data.startswith("bb:"):
                self.answer(query_id, "Эта старая кнопка больше не используется")
            else:
                self.answer(query_id, "Неизвестная команда")
        except Exception as exc:
            print(f"ERROR callback {data}: {type(exc).__name__}: {exc}")
            self.answer(query_id, "Ошибка выполнения")
            self.send(
                f"⚠️ Не удалось выполнить команду: "
                f"<code>{html.escape(type(exc).__name__)}</code>."
            )

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, dict):
            self.handle_message(message)
            return
        query = update.get("callback_query")
        if isinstance(query, dict):
            self.handle_callback(query)

    def run(self) -> int:
        if not BOT_TOKEN or not BOT_CHAT_ID or not GITHUB_TOKEN or not GITHUB_REPOSITORY:
            raise RuntimeError(
                "BOT_TOKEN, BOT_CHAT_ID, GITHUB_TOKEN and GITHUB_REPOSITORY are required"
            )
        self.setup_bot()
        print(f"Admin bot started for {GITHUB_REPOSITORY}; run_seconds={RUN_SECONDS}")
        deadline = time.monotonic() + RUN_SECONDS
        while time.monotonic() < deadline:
            try:
                payload: dict[str, Any] = {
                    "timeout": 25,
                    "allowed_updates": ["message", "callback_query"],
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
                if self.offset is not None and response.get("result"):
                    self.telegram_api(
                        "getUpdates",
                        {
                            "offset": self.offset,
                            "timeout": 0,
                            "allowed_updates": ["message", "callback_query"],
                        },
                    )
            except requests.RequestException as exc:
                print(f"WARNING polling network error: {type(exc).__name__}: {exc}")
                time.sleep(5)
            except Exception as exc:
                print(f"WARNING polling error: {type(exc).__name__}: {exc}")
                time.sleep(3)
        print("Admin bot shift completed normally.")
        return 0


def self_test() -> None:
    text = "# test\nalpha\n@Beta # comment\nalpha\n"
    assert AdminBot.parse_list(text) == ["alpha", "Beta"]
    removed = AdminBot.remove_from_list_text(text, "beta")
    assert "@Beta" not in removed
    appended = AdminBot.append_to_list_text(removed, "gamma")
    assert AdminBot.parse_list(appended) == ["alpha", "gamma"]
    assert AdminBot.safe_source("@gazazor/123") == "gazazor"
    assert USERNAME_RE.fullmatch("gazazor")
    assert not USERNAME_RE.fullmatch("bad-name")
    bot = AdminBot()
    stats = {
        "daily": {
            datetime.now(DISPLAY_TZ).date().isoformat(): {
                "totals": {"checks": 10, "wheel_posts": 2}
            }
        }
    }
    totals = bot.period_totals(stats, 1)
    assert totals["checks"] == 10 and totals["wheel_posts"] == 2
    print("admin_bot self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return AdminBot().run()


if __name__ == "__main__":
    raise SystemExit(main())
