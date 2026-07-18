from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from admin_panel_v2 import TelegramPanelV2

CANDIDATES_PER_PAGE = 5
BTN_APP = "📱 Приложение"
BTN_NIGHTLY = "🌙 Ночное наблюдение"
BTN_INTELLIGENCE = "🛰️ Разведка источников"

ADMIN_KEYBOARD_V9 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": BTN_NIGHTLY}],
        [{"text": BTN_INTELLIGENCE}, {"text": "⚙️ Настройки"}],
        [{"text": BTN_APP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Панель BetBoom Monitor",
}

USER_KEYBOARD_V9 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": BTN_APP}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "BetBoom Monitor",
}

BRAND_NAME = "BB V.G."
MINIAPP_RELEASE = "5.11.0"
MINIAPP_URL = "https://slavagostev2-betboom-monitor.pages.dev/"
MINIMAL_COMMANDS = [
    {"command": "start", "description": "Открыть панель"},
    {"command": "myid", "description": "Показать мой Telegram ID"},
]
DEPLOYMENT_PATH = "miniapp_deployment.json"
MODERATION_PATH = "candidate_moderation.json"
INTELLIGENCE_PATH = "intelligence_state.json"
UTC = timezone.utc


class PanelFoundationMixin:
    """Foundational Telegram panel behavior owned by the consolidated package.

    The mixin expects the storage, Telegram API, navigation stack and candidate
    helpers supplied by the final runtime. It is cooperative: handlers pass
    unmatched messages and callbacks to ``super()``.
    """

    def load_moderation(self) -> dict[str, Any]:
        try:
            value = self.get_json_file(MODERATION_PATH, {"version": 1, "ignored": {}})
        except Exception:
            value = {"version": 1, "ignored": {}}
        if not isinstance(value, dict):
            value = {}
        ignored = value.get("ignored")
        if not isinstance(ignored, dict):
            ignored = {}
        return {
            "version": 1,
            "ignored": {
                str(source).casefold(): dict(entry) if isinstance(entry, dict) else {}
                for source, entry in ignored.items()
                if str(source)
            },
        }

    def save_moderation(self, value: dict[str, Any], message: str) -> None:
        normalized = {"version": 1, "ignored": value.get("ignored", {})}
        self.update_file(
            MODERATION_PATH,
            json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            message,
        )

    @staticmethod
    def candidate_score(entry: dict[str, Any]) -> int:
        found = max(0, int(entry.get("wheel_links_found", 0) or 0))
        recent = max(0, int(entry.get("recent_wheel_links", 0) or 0))
        active = max(0, int(entry.get("active_wheel_links", 0) or 0))
        status = str(entry.get("status") or "")
        score = min(50, found * 6) + min(20, recent * 10) + min(15, active * 15)
        latest = TelegramPanelV2.parse_dt(entry.get("latest_wheel_at"))
        if latest:
            age_days = max(
                0,
                int((datetime.now(UTC) - latest.astimezone(UTC)).total_seconds() // 86400),
            )
            if age_days <= 2:
                score += 15
            elif age_days <= 7:
                score += 10
            elif age_days <= 30:
                score += 5
        if status in {"error", "empty", "quarantined"}:
            score -= 20
        return max(0, min(100, score))

    @staticmethod
    def score_label(score: int) -> str:
        if score >= 70:
            return "🟢 сильный"
        if score >= 40:
            return "🟡 перспективный"
        return "⚪ слабый"

    @staticmethod
    def recommendation(score: int) -> str:
        if score >= 70:
            return "рекомендуется основная проверка"
        if score >= 40:
            return "рекомендуется ночное наблюдение"
        return "нужно дополнительное наблюдение"

    def candidate_rows(self) -> list[dict[str, Any]]:
        snap = self.snapshot()
        ignored = self.load_moderation()["ignored"]
        fast = {source.casefold() for source in snap.fast}
        nightly = {source.casefold() for source in snap.nightly}
        rows: list[dict[str, Any]] = []
        for source, raw in snap.discovery.get("sources", {}).items():
            if not isinstance(raw, dict) or int(raw.get("wheel_links_found", 0) or 0) <= 0:
                continue
            key = str(source).casefold()
            category = (
                "primary"
                if key in fast
                else "ignored"
                if key in ignored
                else "nightly"
                if key in nightly
                else "new"
            )
            item = dict(raw)
            item.update(source=str(source), category=category)
            item["score"] = self.candidate_score(item)
            rows.append(item)
        rows.sort(
            key=lambda item: (
                {"new": 0, "nightly": 1, "ignored": 2, "primary": 3}.get(
                    str(item["category"]), 9
                ),
                -int(item["score"]),
                -int(item.get("wheel_links_found", 0) or 0),
                str(item["source"]).casefold(),
            )
        )
        return rows

    def _candidate_filter(self, category: str) -> list[dict[str, Any]]:
        rows = self.candidate_rows()
        if category == "all":
            return [row for row in rows if row["category"] != "primary"]
        return [row for row in rows if row["category"] == category]

    def _recent_candidate_wheels(self, source: str) -> list[dict[str, Any]]:
        result = [
            dict(entry)
            for entry in self.snapshot().discovery.get("notified_wheels", {}).values()
            if isinstance(entry, dict)
            and str(entry.get("source") or "").casefold() == source.casefold()
        ]
        result.sort(key=lambda item: str(item.get("notified_at") or ""), reverse=True)
        return result[:5]

    def intelligence_state(self) -> dict[str, Any]:
        value = self.get_json_file(
            INTELLIGENCE_PATH,
            {"version": 1, "candidates": {}, "edges": {}, "runs": []},
        )
        return value if isinstance(value, dict) else {}

    def intelligence_rows(self) -> list[dict[str, Any]]:
        state = self.intelligence_state()
        ignored = self.load_moderation().get("ignored", {})
        snap = self.snapshot()
        known = {source.casefold() for source in [*snap.fast, *snap.nightly]}
        rows: list[dict[str, Any]] = []
        for key, raw in state.get("candidates", {}).items():
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source") or key)
            item = dict(raw)
            item["source"] = source
            folded = source.casefold()
            item["decision"] = (
                "known" if folded in known else "ignored" if folded in ignored else "new"
            )
            rows.append(item)
        rows.sort(
            key=lambda item: (
                {"new": 0, "ignored": 1, "known": 2}.get(
                    str(item.get("decision")), 9
                ),
                -int(item.get("score", 0) or 0),
                -int(item.get("wheel_links_found", 0) or 0),
                str(item.get("source") or "").casefold(),
            )
        )
        return rows

    @staticmethod
    def intelligence_label(score: int, wheels: int) -> str:
        if wheels > 0 and score >= 60:
            return "🟢 подтверждённый"
        if score >= 35:
            return "🟡 связанный"
        return "⚪ слабый сигнал"

    def filtered_intelligence_rows(self, category: str) -> list[dict[str, Any]]:
        rows = self.intelligence_rows()
        if category == "wheels":
            return [
                row
                for row in rows
                if row.get("decision") == "new"
                and int(row.get("wheel_links_found", 0) or 0) > 0
            ]
        if category in {"new", "ignored"}:
            return [row for row in rows if row.get("decision") == category]
        return rows

    def setup_bot(self) -> None:
        self.telegram_api("deleteWebhook", {"drop_pending_updates": False})
        self.telegram_api("setMyCommands", {"commands": MINIMAL_COMMANDS})
        self.telegram_api("setChatMenuButton", {"menu_button": {"type": "commands"}})

    def miniapp_deployment(self) -> dict[str, Any]:
        try:
            value = self.get_json_file(
                DEPLOYMENT_PATH,
                {"status": "awaiting_cloudflare_secrets", "url": ""},
            )
        except Exception:
            value = {"status": "unknown", "url": ""}
        return value if isinstance(value, dict) else {}

    def bot_username(self) -> str:
        cached = getattr(self, "_bot_username_cache", None)
        if cached is not None:
            return str(cached)
        try:
            result = self.telegram_api("getMe").get("result") or {}
            username = str(result.get("username") or "").strip().lstrip("@")
        except Exception as exc:
            print(f"WARNING get bot username: {type(exc).__name__}: {exc}")
            username = ""
        self._bot_username_cache = username
        return username

    def miniapp_url_for_chat(self) -> str:
        deployment = self.miniapp_deployment()
        deployed = str(deployment.get("url") or "").strip()
        base = (
            deployed
            if deployment.get("status") == "deployed" and deployed.startswith("https://")
            else MINIAPP_URL
        )
        params = [f"release={MINIAPP_RELEASE}"]
        username = self.bot_username()
        if username:
            params.append(f"bot={quote(username)}")
        separator = "&" if "?" in base else "?"
        return base + separator + "&".join(params)

    def show_app_entry(self) -> None:
        url = self.miniapp_url_for_chat()
        self.send(
            f"📱 <b>Приложение {BRAND_NAME}</b>\n\n"
            "Актуальные колёса, статистика, источники и запросы пользователей.",
            reply_markup=self.with_nav(
                [
                    [{"text": "📱 Открыть внутри Telegram", "web_app": {"url": url}}],
                    [{"text": "🌐 Открыть в браузере", "url": url}],
                ]
            ),
        )

    def show_discovery(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot(force=True)
        rows = self.candidate_rows()
        new_rows = [row for row in rows if row.get("category") == "new"]
        nightly_with_wheels = [row for row in rows if row.get("category") == "nightly"]
        try:
            run = self.workflow_run("nightly-discovery.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if not snap.nightly:
            state_text = "⚪ не требуется — ночной список пуст"
        elif status == "in_progress":
            state_text = "🔵 ночная проверка выполняется"
        elif status in {"queued", "waiting", "pending"}:
            state_text = "🟡 ожидает запуска"
        elif status == "completed" and conclusion == "success":
            state_text = "🟢 последняя проверка завершена"
        elif conclusion:
            state_text = "🔴 последняя проверка завершилась с ошибкой"
        else:
            state_text = "⚪ данных о запуске нет"
        discovery_keys = {str(value).casefold() for value in snap.discovery.get("sources", {})}
        checked = sum(1 for name in snap.nightly if name.casefold() in discovery_keys)
        text = (
            "🌙 <b>Ночное наблюдение</b>\n\n"
            f"Состояние: {state_text}\n"
            f"Последнее завершение: {self.fmt_dt(snap.discovery.get('last_run_at'))}\n"
            f"Проверено: <b>{checked} из {len(snap.nightly)}</b>\n\n"
            f"Источников в ночном режиме: <b>{len(snap.nightly)}</b>\n"
            f"С найденными колёсами: <b>{len(nightly_with_wheels)}</b>\n"
            f"Новых кандидатов: <b>{len(new_rows)}</b>"
        )
        buttons = [
            [{"text": f"🆕 Требуют решения ({len(new_rows)})", "callback_data": "candidate:list:new:0"}],
            [{"text": f"🎡 С колёсами ({len(nightly_with_wheels)})", "callback_data": "candidate:list:nightly:0"}],
        ]
        if snap.nightly:
            buttons.append([{"text": "▶️ Запустить ночную проверку", "callback_data": "control:nightly"}])
        buttons.append([{"text": "🔄 Обновить состояние", "callback_data": "page:discovery"}])
        self.send(text, reply_markup=self.with_nav(buttons))

    def show_candidate_list(self, category: str, page: int = 0) -> None:
        if category != "nightly":
            super().show_candidate_list(category, page)
            return
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        rows = self._candidate_filter(category)
        max_page = max(0, (len(rows) - 1) // CANDIDATES_PER_PAGE)
        page = max(0, min(page, max_page))
        part = rows[
            page * CANDIDATES_PER_PAGE : (page + 1) * CANDIDATES_PER_PAGE
        ]
        lines = [
            "🎡 <b>Каналы ночной базы, где находились колёса</b>",
            f"Страница {page + 1} из {max_page + 1}",
            "",
        ]
        buttons: list[list[dict[str, str]]] = []
        for item in part:
            source = str(item.get("source") or "")
            score = int(item.get("score", 0) or 0)
            found = int(item.get("wheel_links_found", 0) or 0)
            lines.extend(
                [
                    f"<b>@{html.escape(source)}</b>",
                    f"{self.score_label(score)} · оценка {score}/100",
                    f"Найдено колёс: {found} · последнее: "
                    f"{self.fmt_dt(item.get('latest_wheel_at'))}",
                    "",
                ]
            )
            buttons.append(
                [
                    {
                        "text": f"@{source[:24]} · колёс {found}",
                        "callback_data": f"candidate:detail:{source}",
                    }
                ]
            )
        if not part:
            lines.append("В ночной базе пока нет каналов с найденными колёсами.")
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append(
                {
                    "text": "◀️",
                    "callback_data": f"candidate:list:nightly:{page - 1}",
                }
            )
        if page < max_page:
            nav.append(
                {
                    "text": "▶️",
                    "callback_data": f"candidate:list:nightly:{page + 1}",
                }
            )
        if nav:
            buttons.append(nav)
        buttons.append(
            [
                {
                    "text": "🌙 К сводке ночного наблюдения",
                    "callback_data": "page:discovery",
                }
            ]
        )
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    @staticmethod
    def _callback_page(data: str) -> str | None:
        if data.startswith("page:"):
            return data[5:]
        return None

    def nav_rows(self) -> list[list[dict[str, str]]]:
        stack = self.stack()
        previous = stack[-2] if len(stack) >= 2 else None
        if previous in {None, "menu"}:
            return [[{"text": "🏠 Главное меню", "callback_data": "nav:home"}]]
        return [
            [
                {"text": "⬅️ Назад", "callback_data": "nav:back"},
                {"text": "🏠 Главное меню", "callback_data": "nav:home"},
            ]
        ]

    def with_nav(
        self,
        rows: list[list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        stack = self.stack()
        previous = stack[-2] if len(stack) >= 2 else None
        cleaned: list[list[dict[str, str]]] = []
        seen: set[tuple[str, str]] = set()

        for row in rows or []:
            kept: list[dict[str, str]] = []
            for button in row:
                data = str(button.get("callback_data") or "")
                target = self._callback_page(data)
                if data in {"nav:back", "nav:home"}:
                    continue
                if target == "menu":
                    continue
                if previous and target == previous:
                    continue

                if data:
                    identity = ("callback", data)
                elif button.get("url"):
                    identity = ("url", str(button.get("url")))
                elif button.get("web_app"):
                    identity = ("web_app", str(button.get("web_app")))
                else:
                    identity = ("text", str(button.get("text") or ""))
                if identity in seen:
                    continue
                seen.add(identity)
                kept.append(button)
            if kept:
                cleaned.append(kept)
        return {"inline_keyboard": cleaned + self.nav_rows()}

    @staticmethod
    def intelligence_launch_text() -> str:
        return (
            "▶️ <b>Разведка новых источников запущена</b>\n\n"
            "Состояние: 🟡 запрос передан в GitHub Actions и ожидает начала выполнения.\n"
            "После запуска строка состояния в сводке изменится на «разведка выполняется», "
            "а после завершения — на результат последнего запуска."
        )

    def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        command = (
            text.split("@", 1)[0].split(maxsplit=1)[0].casefold() if text else ""
        )
        if command == "/myid":
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            self.set_context(chat.get("id"), sender.get("id"))
            self.send(
                f"🆔 Ваш Telegram ID: <code>{self.current_user_id or ''}</code>",
                reply_markup=self.with_nav(),
            )
            return
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data != "control:intelligence":
            super().handle_callback(query)
            return

        message = query.get("message") or {}
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        self.set_context(chat.get("id"), sender.get("id"))
        query_id = str(query.get("id") or "")
        if not self.is_admin():
            self.answer(query_id, "Недостаточно прав")
            return

        try:
            self.dispatch("source-intelligence.yml", None)
        except Exception as exc:
            self.answer(query_id, "Ошибка запуска")
            self.send(
                "⚠️ Не удалось запустить разведку: "
                f"<code>{html.escape(type(exc).__name__)}</code>.",
                reply_markup=self.with_nav(),
            )
            return

        self.answer(query_id, "Разведка запущена")
        self.send(
            self.intelligence_launch_text(),
            reply_markup=self.with_nav(
                [
                    [
                        {
                            "text": "🔄 Обновить состояние",
                            "callback_data": "page:intelligence",
                        }
                    ]
                ]
            ),
        )


class _FoundationTestPanel(PanelFoundationMixin, TelegramPanelV2):
    def __init__(self) -> None:
        self.current_user_id = "1"
        self.current_chat_id = "1"
        self.navigation = {"1": ["menu"]}


def self_test() -> None:
    assert BRAND_NAME == "BB V.G."
    assert MINIAPP_RELEASE == "5.11.0"
    assert MINIAPP_URL.endswith(".pages.dev/")
    assert [item["command"] for item in MINIMAL_COMMANDS] == ["start", "myid"]
    assert BTN_APP in str(ADMIN_KEYBOARD_V9)
    assert BTN_APP in str(USER_KEYBOARD_V9)
    assert DEPLOYMENT_PATH == "miniapp_deployment.json"

    panel = _FoundationTestPanel()
    panel.navigation["1"] = ["menu", "discovery"]
    assert panel.with_nav([])["inline_keyboard"] == [
        [{"text": "🏠 Главное меню", "callback_data": "nav:home"}]
    ]
    panel.navigation["1"] = ["menu", "discovery", "candidate:list:nightly:0"]
    nested = panel.with_nav(
        [
            [
                {
                    "text": "🌙 К сводке ночного наблюдения",
                    "callback_data": "page:discovery",
                }
            ],
            [{"text": "@channel", "callback_data": "candidate:detail:channel"}],
            [
                {
                    "text": "@channel duplicate",
                    "callback_data": "candidate:detail:channel",
                }
            ],
        ]
    )["inline_keyboard"]
    callbacks = [button.get("callback_data") for row in nested for button in row]
    assert "page:discovery" not in callbacks
    assert callbacks.count("candidate:detail:channel") == 1
    assert "nav:back" in callbacks and "nav:home" in callbacks

    launch_text = panel.intelligence_launch_text()
    assert "Состояние:" in launch_text
    assert "ожидает начала выполнения" in launch_text

    sent: list[tuple[str, dict[str, Any]]] = []
    panel.set_context = lambda chat_id, user_id: setattr(
        panel,
        "current_user_id",
        str(user_id),
    )  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: sent.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.handle_message(
        {"text": "/myid", "chat": {"id": 10}, "from": {"id": 20}}
    )
    assert sent and "<code>20</code>" in sent[-1][0]

    panel.telegram_api = lambda method, payload=None: {
        "ok": True,
        "result": {"username": "test_bot"},
    }  # type: ignore[method-assign]
    panel.miniapp_deployment = lambda: {
        "status": "deployed",
        "url": "https://example.com/app",
    }  # type: ignore[method-assign]
    panel.show_app_entry()
    app_text, app_kwargs = sent[-1]
    app_url = app_kwargs["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert "Приложение BB V.G." in app_text
    assert app_url == "https://example.com/app?release=5.11.0&bot=test_bot"

    assert issubclass(_FoundationTestPanel, TelegramPanelV2)
    print("BB V.G. panel foundation self-test passed")


if __name__ == "__main__":
    self_test()
