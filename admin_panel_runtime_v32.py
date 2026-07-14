from __future__ import annotations

import argparse
import html
from datetime import datetime, timezone
from typing import Any

import admin_action_v3  # noqa: F401 - patches the inherited direct action runtime
from admin_bot import COMMANDS, DISPLAY_TZ
from admin_panel_runtime_v31 import SUMMARY_PERIODS, TelegramPanelRuntimeV31


UTC = timezone.utc


class TelegramPanelRuntimeV32(TelegramPanelRuntimeV31):
    """Compact wheel controls, unified analytics and role-safe system status."""

    def setup_bot(self) -> None:
        super().setup_bot()
        commands = [dict(item) for item in COMMANDS]
        for item in commands:
            if item.get("command") == "reports":
                item["description"] = "Аналитика"
        self.telegram_api("setMyCommands", {"commands": commands})

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [
            [
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
                {"text": "📊 Аналитика", "callback_data": "page:analytics"},
            ],
            [
                {"text": "📡 Источники", "callback_data": "page:sources"},
                {"text": "⚙️ Настройки", "callback_data": "page:settings"},
            ],
        ]
        if admin:
            rows.append([{"text": "🛠 Управление", "callback_data": "page:control"}])
        return rows

    def _collect_current_wheels(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        return [
            item
            for item in super()._collect_current_wheels()
            if (
                self.parse_dt(item.get("deadline")) is None
                or self.parse_dt(item.get("deadline")).astimezone(UTC) > now
            )
        ]

    @staticmethod
    def _sources_for_item(
        snap: Any,
        key: str,
        item: dict[str, Any],
    ) -> list[str]:
        result: list[str] = []
        rows = snap.state.get("wheel_publications", {}).get(key.casefold(), [])
        if isinstance(rows, list):
            result.extend(
                str(row.get("source") or "").strip().lstrip("@")
                for row in rows
                if isinstance(row, dict)
            )
        raw_sources = item.get("sources")
        if isinstance(raw_sources, list):
            result.extend(str(value).strip().lstrip("@") for value in raw_sources)
        result.append(str(item.get("source") or "").strip().lstrip("@"))
        seen: set[str] = set()
        unique: list[str] = []
        for source in result:
            if source and source.casefold() not in seen:
                seen.add(source.casefold())
                unique.append(source)
        return unique

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot(force=True)
        participating = self._joined_wheel_keys(snap)
        if not items:
            self.send(
                "🔥 <b>Активных колёс сейчас нет.</b>",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить", "callback_data": "refresh:active"}]]
                ),
            )
            return

        admin = self.is_admin()
        lines = [f"🔥 <b>Активные колёса: {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier).casefold()
            deadline = self.parse_dt(item.get("deadline"))
            joined = identifier.casefold() in participating or key in participating
            sources = self._sources_for_item(snap, key, item)
            source_text = ", ".join(f"@{source}" for source in sources) or "источник неизвестен"
            time_text = self.remaining(deadline) if deadline else "время не найдено"
            joined_text = "✅ участвуете" if joined else "❌ участие не отмечено"
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b> — {html.escape(time_text)}",
                    f"   📡 {html.escape(source_text)} · {joined_text}",
                ]
            )

            row: list[dict[str, str]] = []
            url = str(item.get("url") or "")
            if url:
                row.append(
                    {
                        "text": f"🎡 {index} · {identifier[:12]}",
                        "url": url,
                    }
                )
            if not joined:
                row.append(
                    {
                        "text": "✅ Участвую",
                        "callback_data": f"wheel:part:{key}",
                    }
                )
            if admin:
                row.append(
                    {
                        "text": "🏁 Завершилось",
                        "callback_data": f"wheel:finished:{key}",
                    }
                )
            else:
                row.append(
                    {
                        "text": "🙈 Скрыть",
                        "callback_data": f"wheel:inactive:{key}",
                    }
                )
            if row:
                buttons.append(row)
            if admin and deadline is None:
                buttons.append(
                    [
                        {
                            "text": f"⏱ Указать время для {index}",
                            "callback_data": f"wheel:time:{key}",
                        }
                    ]
                )

        buttons.append([{"text": "🔄 Обновить", "callback_data": "refresh:active"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    def show_analytics(self, days: int = 1) -> None:
        days = days if days in {1, 7, 30} else 1
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        lines = [f"📊 <b>Аналитика {self.period_title(days)}</b>", ""]

        if overview["wheel_posts"]:
            lines.append(f"🎡 Публикаций с колёсами: <b>{overview['wheel_posts']}</b>")
            lines.append(f"📡 Источников с находками: <b>{overview['sources_with_wheels']}</b>")
            if overview["notifications"]:
                lines.append(f"🔔 Уведомлений: <b>{overview['notifications']}</b>")
            if days > 1:
                lines.append(
                    f"📈 Среднее за день: <b>{overview['wheel_posts'] / days:.1f}</b>"
                )
            if overview["top_sources"]:
                lines.extend(["", "<b>Лучшие источники</b>"])
                for index, (source, count) in enumerate(overview["top_sources"][:5], 1):
                    lines.append(f"{index}. @{html.escape(source)} — {count}")
        else:
            lines.append("За выбранный период новые публикации с колёсами не найдены.")

        lines.extend(["", "<b>Сейчас</b>"])
        lines.append(f"🔥 Активных колёс: <b>{overview['active']}</b>")
        if overview["active"]:
            lines.append(
                f"⏱ С известным временем: <b>{overview['active_with_time']} из {overview['active']}</b>"
            )
            lines.append(
                f"✅ Участие отмечено: <b>{overview['participating']} из {overview['active']}</b>"
            )

        rows: list[list[dict[str, str]]] = [
            [
                {"text": "Сегодня", "callback_data": "page:analytics:1"},
                {"text": "7 дней", "callback_data": "page:analytics:7"},
                {"text": "30 дней", "callback_data": "page:analytics:30"},
            ]
        ]
        if self.is_admin():
            rows.extend(
                [
                    [{"text": "📨 Отправить сводку", "callback_data": "summary:send"}],
                    [{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}],
                ]
            )
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_stats(self, days: int = 1) -> None:
        self.show_analytics(days)

    def show_reports(self) -> None:
        self.show_analytics(1)

    def show_period_report(self, days: int) -> None:
        self.show_analytics(days)

    def render_page(self, page: str) -> None:
        if page == "analytics":
            self.show_analytics(1)
            return
        if page.startswith("analytics:"):
            value = page.split(":", 1)[1]
            self.show_analytics(int(value) if value.isdigit() else 1)
            return
        if page.startswith("stats:"):
            value = page.split(":", 1)[1]
            self.show_analytics(int(value) if value.isdigit() else 1)
            return
        if page == "reports":
            self.show_analytics(1)
            return
        if page.startswith("report:"):
            value = page.split(":", 1)[1]
            if value == "inactive":
                if not self.is_admin():
                    self.send("Раздел доступен только администраторам.", reply_markup=self.with_nav())
                else:
                    self.show_inactive_report()
                return
            if value.isdigit():
                self.show_analytics(int(value))
                return
        if page == "status" and not self.is_admin():
            self.show_menu(clear_stack=True)
            return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data.startswith("wheel:finished:"):
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            if not self.is_admin():
                self.answer(query_id, "Недоступно")
                return
            key = data.split(":", 2)[2].casefold()
            self.answer(query_id, "Сохраняю завершение")
            try:
                result = self.dispatch_admin_action(
                    "confirm_finished_global",
                    f"{key}|{self.current_user_id or 'admin'}",
                )
                self.refresh_snapshot()
            except Exception as exc:
                self.send(
                    "⚠️ <b>Не удалось завершить колесо.</b>\n\n"
                    f"Ошибка: <code>{html.escape(type(exc).__name__)}</code>."
                )
                return
            self.send(f"✅ <b>Колесо завершено</b>\n{html.escape(str(result.get('detail') or ''))}")
            self.show_active()
            return
        if data.startswith("candidate:defer:"):
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            if not self.is_admin():
                self.answer(query_id, "Недоступно")
                return
            source = data.split(":", 2)[2]
            self.answer(query_id, "Оставлено в списке")
            self.send(
                f"⏸ @{html.escape(source)} пока не добавлен и остаётся в списке кандидатов.",
                reply_markup=self.with_nav(),
            )
            return
        super().handle_callback(query)


def _callbacks(rows: list[list[dict[str, Any]]]) -> list[str]:
    return [str(button.get("callback_data") or "") for row in rows for button in row]


def self_test() -> None:
    admin_main = _callbacks(TelegramPanelRuntimeV32.compact_menu_rows(True))
    user_main = _callbacks(TelegramPanelRuntimeV32.compact_menu_rows(False))
    assert set(admin_main) == {
        "page:active",
        "page:analytics",
        "page:sources",
        "page:settings",
        "page:control",
    }
    assert set(user_main) == {
        "page:active",
        "page:analytics",
        "page:sources",
        "page:settings",
    }
    assert "page:status" not in user_main
    assert "page:reports" not in admin_main
    assert SUMMARY_PERIODS["monthly"][0] == 30
    assert admin_action_v3.legacy.apply_action is admin_action_v3.apply_action_v3
    print("admin panel v32 compact lifecycle and analytics self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV32().run()


if __name__ == "__main__":
    raise SystemExit(main())
