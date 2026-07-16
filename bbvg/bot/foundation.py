from __future__ import annotations

import html
from types import SimpleNamespace
from typing import Any

from admin_panel_runtime_v5 import CANDIDATES_PER_PAGE
from admin_panel_runtime_v9 import (
    ADMIN_KEYBOARD_V9,
    BTN_APP,
    TelegramPanelRuntimeV9,
    USER_KEYBOARD_V9,
)

MINI_APP_CDN_URL = (
    "https://cdn.jsdelivr.net/gh/"
    "slavagostev2-dot/betboom-wheel-monitor@main/docs/index.html"
)
MINIMAL_COMMANDS = [
    {"command": "start", "description": "Открыть панель"},
    {"command": "myid", "description": "Показать мой Telegram ID"},
]
DEPLOYMENT_PATH = "miniapp_deployment.json"
CLOUDFLARE_APP_URL = "https://slavagostev2-betboom-monitor.pages.dev/"
FALLBACK_APP_URL = (
    "https://raw.githack.com/slavagostev2-dot/"
    "betboom-wheel-monitor/main/docs/index.html"
)


class PanelFoundationMixin:
    """Foundational Telegram panel behavior formerly spread across v10-v13.

    The mixin expects the storage, Telegram API, navigation stack and candidate
    helpers supplied by ``TelegramPanelRuntimeV9``. It is cooperative: handlers
    pass unmatched messages and callbacks to ``super()``.
    """

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

    def show_app_entry(self) -> None:
        deployment = self.miniapp_deployment()
        status = str(deployment.get("status") or "unknown")
        deployed_url = str(deployment.get("url") or "").strip()
        if status == "deployed" and deployed_url.startswith("https://"):
            text = (
                "📱 <b>Приложение BetBoom Monitor</b>\n\n"
                "Основная версия опубликована на Cloudflare Pages."
            )
            rows = [
                [{"text": "📱 Открыть внутри Telegram", "web_app": {"url": deployed_url}}],
                [{"text": "🌐 Открыть в браузере", "url": deployed_url}],
            ]
        else:
            text = (
                "📱 <b>Приложение BetBoom Monitor</b>\n\n"
                "Cloudflare Pages подготовлен, но ещё не авторизован секретами GitHub. "
                "Пока доступна резервная HTML-версия."
            )
            rows = [
                [{"text": "📱 Открыть резервную версию", "web_app": {"url": FALLBACK_APP_URL}}],
                [{"text": "🌐 Открыть в браузере", "url": FALLBACK_APP_URL}],
            ]
        self.send(text, reply_markup=self.with_nav(rows))

    def show_discovery(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot()
        rows = self.candidate_rows()
        new_rows = [row for row in rows if row.get("category") == "new"]
        nightly_with_wheels = [row for row in rows if row.get("category") == "nightly"]
        ignored_rows = [row for row in rows if row.get("category") == "ignored"]
        strong_new = sum(int(row.get("score", 0) or 0) >= 70 for row in new_rows)

        try:
            run = self.workflow_run("nightly-discovery.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status in {"queued", "waiting", "pending"}:
            status_text = "🟡 ожидает запуска"
        elif status == "in_progress":
            status_text = "🔵 ночная проверка выполняется"
        elif status == "completed" and conclusion == "success":
            status_text = "🟢 последняя ночная проверка завершена"
        elif conclusion:
            status_text = f"🔴 завершена с результатом: {conclusion}"
        else:
            status_text = "⚪ данных о запуске нет"

        discovery_keys = {
            str(value).casefold() for value in snap.discovery.get("sources", {})
        }
        checked = sum(1 for name in snap.nightly if name.casefold() in discovery_keys)
        text = (
            "🌙 <b>Ночное наблюдение</b>\n\n"
            f"Состояние: {html.escape(status_text)}\n"
            f"Последнее завершение: {self.fmt_dt(snap.discovery.get('last_run_at'))}\n"
            f"Проверено в последнем сохранённом запуске: {checked} из {len(snap.nightly)}\n\n"
            f"🌙 Всего каналов в ночной базе: <b>{len(snap.nightly)}</b>\n"
            f"🎡 Из них публиковали колёса: <b>{len(nightly_with_wheels)}</b>\n"
            f"🆕 Новых каналов вне базы, требующих решения: <b>{len(new_rows)}</b>\n"
            f"🟢 Сильных новых кандидатов: <b>{strong_new}</b>\n"
            f"🙈 Игнорируются: <b>{len(ignored_rows)}</b>\n\n"
            "Каналы из ночной базы остаются в ней. Новые неизвестные каналы "
            "не добавляются никуда без решения администратора."
        )
        buttons = [
            [
                {
                    "text": f"🆕 Требуют решения ({len(new_rows)})",
                    "callback_data": "candidate:list:new:0",
                }
            ],
            [
                {
                    "text": f"🎡 С колёсами в ночной базе ({len(nightly_with_wheels)})",
                    "callback_data": "candidate:list:nightly:0",
                }
            ],
            [
                {
                    "text": f"🙈 Игнорируемые ({len(ignored_rows)})",
                    "callback_data": "candidate:list:ignored:0",
                }
            ],
            [
                {
                    "text": "▶️ Запустить ночную проверку",
                    "callback_data": "control:nightly",
                }
            ],
        ]
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


class _FoundationTestPanel(PanelFoundationMixin, TelegramPanelRuntimeV9):
    def __init__(self) -> None:
        self.current_user_id = "1"
        self.current_chat_id = "1"
        self.navigation = {"1": ["menu"]}


def self_test() -> None:
    assert MINI_APP_CDN_URL.startswith("https://cdn.jsdelivr.net/")
    assert [item["command"] for item in MINIMAL_COMMANDS] == ["start", "myid"]
    assert BTN_APP in str(ADMIN_KEYBOARD_V9)
    assert BTN_APP in str(USER_KEYBOARD_V9)
    assert CLOUDFLARE_APP_URL.endswith(".pages.dev/")
    assert FALLBACK_APP_URL.startswith("https://raw.githack.com/")
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
    panel.set_context = lambda chat_id, user_id: setattr(panel, "current_user_id", str(user_id))  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: sent.append((text, kwargs)) or {}  # type: ignore[method-assign]
    panel.handle_message(
        {"text": "/myid", "chat": {"id": 10}, "from": {"id": 20}}
    )
    assert sent and "<code>20</code>" in sent[-1][0]

    panel.miniapp_deployment = lambda: {"status": "deployed", "url": "https://example.com/app"}  # type: ignore[method-assign]
    panel.show_app_entry()
    assert "Cloudflare Pages" in sent[-1][0]
    assert sent[-1][1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == "https://example.com/app"

    assert issubclass(_FoundationTestPanel, TelegramPanelRuntimeV9)
    print("BB V.G. panel foundation self-test passed")


if __name__ == "__main__":
    self_test()
