from __future__ import annotations

import argparse
import html
from datetime import datetime
from typing import Any

import admin_bot as legacy
from admin_panel_runtime_v21 import MINIAPP_URL, TelegramPanelRuntimeV21

MINIAPP_RELEASE = "5.11.0"
CONFIRMED_POINTS = 40
INACTIVE_POINTS = -45


class TelegramPanelRuntimeV22(TelegramPanelRuntimeV21):
    """BB V.G. v22: unified sources, compact sections and admin-owned rating."""

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [
            [
                {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
            ],
            [
                {"text": "📡 Источники", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг источников", "callback_data": "page:ranking"},
            ],
            [
                {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                {"text": "📱 Приложение", "callback_data": "page:app"},
            ],
        ]
        return rows

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
            from urllib.parse import quote

            params.append(f"bot={quote(username)}")
        separator = "&" if "?" in base else "?"
        return base + separator + "&".join(params)

    def load_source_registry(self) -> dict[str, Any]:
        try:
            value = self.get_json_file(
                "source_registry.json",
                {"version": 2, "summary": {}, "sources": []},
            )
        except Exception:
            value = {"version": 2, "summary": {}, "sources": []}
        return value if isinstance(value, dict) else {"version": 2, "summary": {}, "sources": []}

    def source_registry_fallback(self) -> dict[str, Any]:
        snap = self.snapshot(force=True)
        configured: dict[str, tuple[str, str]] = {}
        for tier, values in (("primary", snap.fast), ("nightly", snap.nightly)):
            for value in values:
                username = str(value or "").strip().lstrip("@")
                if username:
                    configured.setdefault(username.casefold(), (username, tier))
        health_sources = snap.health.get("sources", {}) if isinstance(snap.health, dict) else {}
        rows: list[dict[str, Any]] = []
        for username, tier in configured.values():
            health: dict[str, Any] = {}
            if isinstance(health_sources, dict):
                for key, candidate in health_sources.items():
                    if str(key).casefold() == username.casefold() and isinstance(candidate, dict):
                        health = candidate
                        break
            checked = bool(health.get("last_checked_at") or int(health.get("checks", 0) or 0))
            available = str(health.get("status") or "").casefold() == "ok"
            status = "available" if available else ("unavailable" if checked else "pending")
            rows.append(
                {
                    "username": username,
                    "tier": tier,
                    "status": status,
                    "checked": checked,
                    "available": available,
                    "reason": str(
                        health.get("failure_reason")
                        or health.get("last_error")
                        or ("источник доступен" if available else "ожидает первой проверки")
                    ),
                    "last_checked_at": health.get("last_checked_at"),
                }
            )
        return {
            "version": 2,
            "summary": {
                "total": len(rows),
                "primary": sum(row["tier"] == "primary" for row in rows),
                "nightly": sum(row["tier"] == "nightly" for row in rows),
                "checked": sum(bool(row["checked"]) for row in rows),
                "available": sum(bool(row["available"]) for row in rows),
                "unavailable": sum(row["status"] == "unavailable" for row in rows),
                "pending": sum(row["status"] == "pending" for row in rows),
            },
            "sources": rows,
        }

    def show_sources(self) -> None:
        registry = self.load_source_registry()
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        if not int(summary.get("total", 0) or 0):
            registry = self.source_registry_fallback()
            summary = registry["summary"]
        sources = registry.get("sources") if isinstance(registry.get("sources"), list) else []
        problems = [
            row
            for row in sources
            if isinstance(row, dict) and str(row.get("status") or "") != "available"
        ]
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"Всего в едином реестре: <b>{int(summary.get('total', 0) or 0)}</b>",
            f"Проверено: <b>{int(summary.get('checked', 0) or 0)}</b>",
            f"Доступно: <b>{int(summary.get('available', 0) or 0)}</b>",
            f"Недоступно: <b>{int(summary.get('unavailable', 0) or 0)}</b>",
            f"Ожидает первой проверки: <b>{int(summary.get('pending', 0) or 0)}</b>",
            "",
            "Основной и ночной режимы больше не считаются отдельными базами: каждый источник отображается один раз.",
        ]
        if problems:
            lines.extend(["", "<b>Требуют внимания</b>"])
            for row in problems[:12]:
                username = str(row.get("username") or "неизвестно")
                reason = str(row.get("reason") or "нет данных")[:180]
                lines.append(f"• @{html.escape(username)} — {html.escape(reason)}")
            if len(problems) > 12:
                lines.append(f"• ещё {len(problems) - 12}")
        rows = [
            [
                {"text": "🔄 Обновить реестр", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
            ],
            [{"text": "📱 Открыть полный список", "callback_data": "page:app"}],
        ]
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot()
        totals = self.period_totals(snap.stats, days)
        title = "сегодня" if days == 1 else f"за {days} дней"
        confirmed = int(totals.get("admin_confirmed_wheels", 0) or 0)
        inactive = int(totals.get("admin_rejected_wheels", 0) or 0)
        if not confirmed and not inactive:
            confirmed = int(totals.get("activation_sent", 0) or 0)
        lines = [
            f"📊 <b>BB V.G.: статистика {title}</b>",
            "",
            f"Проверок источников: {int(totals.get('checks', 0) or 0)}",
            f"Просмотрено сообщений: {int(totals.get('messages_scanned', 0) or 0)}",
            f"Найдено постов с колёсами: {int(totals.get('wheel_posts', 0) or 0)}",
            f"Подтверждено администратором: {confirmed}",
            f"Признано неактивными: {inactive}",
            f"Повторы уведомлений подавлены: {int(totals.get('duplicates_suppressed', 0) or 0)}",
            "",
            f"Сейчас отображается колёс: {len(self._collect_current_wheels())}",
        ]
        if self.is_admin():
            lines.append(f"Ошибок проверки: {int(totals.get('errors', 0) or 0)}")
        rows: list[list[dict[str, str]]] = [
            [
                {"text": "Сегодня", "callback_data": "page:stats:1"},
                {"text": "7 дней", "callback_data": "page:stats:7"},
                {"text": "30 дней", "callback_data": "page:stats:30"},
            ],
            [{"text": "🏆 Рейтинг источников", "callback_data": "page:ranking"}],
        ]
        if self.is_admin():
            rows.append([{"text": "📨 Отправить ежедневную сводку", "callback_data": "control:daily"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_ranking(self) -> None:
        snap = self.snapshot()
        source_rows = snap.stats.get("sources", {}) if isinstance(snap.stats, dict) else {}
        ranked: list[tuple[str, int, int, int]] = []
        if isinstance(source_rows, dict):
            for source, row in source_rows.items():
                if not isinstance(row, dict):
                    continue
                score = int(row.get("quality_score", 0) or 0)
                confirmed = int(row.get("admin_confirmed_wheels", 0) or 0)
                inactive = int(row.get("admin_rejected_wheels", 0) or 0)
                if score or confirmed or inactive:
                    ranked.append((str(source), score, confirmed, inactive))
        ranked.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            f"Подтверждение администратором: <b>+{CONFIRMED_POINTS}</b> очков.",
            f"Отметка «Неактивное»: <b>{INACTIVE_POINTS}</b> очков.",
            "Личная кнопка пользователя «Участвую» на рейтинг не влияет.",
            "Повторное решение по тому же колесу не начисляет очки повторно.",
            "",
        ]
        for index, (source, score, confirmed, inactive) in enumerate(ranked[:25], 1):
            lines.append(
                f"<b>{index}. @{html.escape(source)}</b> — {score} оч. "
                f"(активных: {confirmed}, неактивных: {inactive})"
            )
        if not ranked:
            lines.append("Рейтинг начнёт формироваться после первого решения администратора.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav([[{"text": "🔄 Обновить рейтинг", "callback_data": "page:ranking"}]]),
        )

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot()
        participating = self._joined_wheel_keys(snap)
        if not items:
            self.send(
                "🔥 <b>BB V.G.: активных колёс сейчас нет.</b>",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить список", "callback_data": "refresh:active"}]]
                ),
            )
            return

        admin = self.is_admin()
        lines = [f"🔥 <b>BB V.G.: активные колёса — {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier).casefold()
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            joined = identifier.casefold() in participating or key in participating
            if deadline:
                time_text = self.remaining(deadline)
            else:
                time_text = "🔴 Время прокрутки неизвестно"
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                    f"⏳ {html.escape(time_text)}",
                    f"📡 @{html.escape(source)}",
                    "✅ Активность подтверждена администратором" if admin and joined else (
                        "✅ Участие отмечено" if joined else "❌ Участие не отмечено"
                    ),
                    "",
                ]
            )
            url = str(item.get("url") or "")
            if url:
                buttons.append([{"text": f"🎡 Открыть {index}", "url": url}])
            actions: list[dict[str, str]] = []
            if not joined:
                actions.append(
                    {
                        "text": "✅ Участвую (+40)" if admin else "✅ Участвую",
                        "callback_data": f"wheel:part:{key}",
                    }
                )
            actions.append(
                {
                    "text": "🚫 Неактивное (−45)" if admin else "🚫 Скрыть у меня",
                    "callback_data": f"wheel:inactive:{key}",
                }
            )
            buttons.append(actions)
            if admin and not deadline:
                buttons.append([{"text": "⏱ Указать время", "callback_data": f"wheel:time:{key}"}])
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "refresh:active"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def render_page(self, page: str) -> None:
        if page in {"discovery", "intelligence", "status", "reports", "pending"}:
            self.show_menu(clear_stack=True)
            return
        if page == "ranking":
            self.show_ranking()
            return
        if page == "sources":
            self.show_sources()
            return
        super().render_page(page)


def self_test() -> None:
    admin_callbacks = {
        button.get("callback_data")
        for row in TelegramPanelRuntimeV22.compact_menu_rows(True)
        for button in row
    }
    assert admin_callbacks == {
        "page:stats:1",
        "page:active",
        "page:sources",
        "page:ranking",
        "page:settings",
        "page:app",
    }
    assert CONFIRMED_POINTS == 40
    assert INACTIVE_POINTS == -45
    assert "Ночное наблюдение" not in str(TelegramPanelRuntimeV22.compact_menu_rows(True))
    assert MINIAPP_RELEASE == "5.11.0"
    print("admin_panel_runtime_v22 points 1-5 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV22().run()


if __name__ == "__main__":
    raise SystemExit(main())
