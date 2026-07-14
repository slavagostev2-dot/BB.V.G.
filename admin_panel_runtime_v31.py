from __future__ import annotations

import argparse
import html
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from admin_bot import COMMANDS, DISPLAY_TZ
from admin_panel_runtime_v30 import TelegramPanelRuntimeV30


SUMMARY_PERIODS = {
    "daily": (1, "Ежедневная"),
    "weekly": (7, "Еженедельная"),
    "monthly": (30, "Ежемесячная"),
}


class TelegramPanelRuntimeV31(TelegramPanelRuntimeV30):
    """Quality-preserving wheel data and concise administrator summaries."""

    def setup_bot(self) -> None:
        super().setup_bot()
        commands = [dict(item) for item in COMMANDS]
        for item in commands:
            if item.get("command") == "reports":
                item["description"] = "Сводки"
        self.telegram_api("setMyCommands", {"commands": commands})

    @staticmethod
    def analytics_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        first = [{"text": "📊 Статистика", "callback_data": "page:stats:1"}]
        if admin:
            first.append({"text": "📅 Сводки", "callback_data": "page:reports"})
        return [
            first,
            [{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}],
        ]

    @staticmethod
    def control_menu_rows() -> list[list[dict[str, Any]]]:
        return [
            [{"text": "▶️ Проверить источники сейчас", "callback_data": "control:monitor"}],
            [{"text": "✅ Состояние системы", "callback_data": "page:status"}],
            [{"text": "🔍 Почему не пришло колесо?", "callback_data": "page:diagnostic"}],
        ]

    @staticmethod
    def summary_send_rows() -> list[list[dict[str, Any]]]:
        return [
            [{"text": "За день", "callback_data": "summary:send:daily"}],
            [{"text": "За неделю", "callback_data": "summary:send:weekly"}],
            [{"text": "За месяц", "callback_data": "summary:send:monthly"}],
        ]

    @staticmethod
    def period_title(days: int) -> str:
        if days == 1:
            return "сегодня"
        if days == 7:
            return "за 7 дней"
        if days == 30:
            return "за 30 дней"
        return f"за {days} дней"

    def period_overview(self, snap: Any, days: int) -> dict[str, Any]:
        totals = self.period_totals(snap.stats, days)
        today = datetime.now(DISPLAY_TZ).date()
        allowed = {(today.isoformat())}
        if days > 1:
            allowed = {
                (today.fromordinal(today.toordinal() - offset)).isoformat()
                for offset in range(days)
            }

        source_counts: dict[str, int] = {}
        day_counts: dict[str, int] = {}
        for day, entry in snap.stats.get("daily", {}).items():
            if day not in allowed or not isinstance(entry, dict):
                continue
            day_total = int((entry.get("totals") or {}).get("wheel_posts", 0) or 0)
            day_counts[day] = day_total
            for source, source_entry in (entry.get("sources") or {}).items():
                if not isinstance(source_entry, dict):
                    continue
                count = int(source_entry.get("wheel_posts", 0) or 0)
                if count > 0:
                    source_counts[str(source)] = source_counts.get(str(source), 0) + count

        top_sources = sorted(
            source_counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        active_rows = [
            (str(key), entry)
            for key, entry in snap.state.get("active_wheels", {}).items()
            if isinstance(entry, dict)
        ]
        active_keys = {
            str(entry.get("identifier") or key).casefold()
            for key, entry in active_rows
        } | {key.casefold() for key, _ in active_rows}
        participating = {
            str(key).casefold()
            for key, entry in snap.state.get("participating_wheels", {}).items()
            if isinstance(entry, dict)
        }
        best_day = max(day_counts.items(), key=lambda item: item[1], default=("", 0))
        wheel_posts = int(totals.get("wheel_posts", 0) or 0)
        notifications = int(totals.get("preliminary_sent", 0) or 0) + int(
            totals.get("activation_sent", 0) or 0
        )
        return {
            "wheel_posts": wheel_posts,
            "notifications": notifications,
            "sources_with_wheels": len(source_counts),
            "top_sources": top_sources,
            "best_day": best_day,
            "active": len(active_rows),
            "active_with_time": sum(
                1 for _, entry in active_rows if self.parse_dt(entry.get("deadline")) is not None
            ),
            "participating": len(active_keys & participating),
        }

    def show_analytics(self) -> None:
        self.send(
            "📊 <b>Аналитика</b>\n\n"
            "Здесь собраны понятные показатели по найденным колёсам и источникам. "
            "Технические счётчики, которые обычно равны нулю, больше не занимают место.",
            reply_markup=self.with_nav(self.analytics_menu_rows(self.is_admin())),
        )

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        lines = [f"📊 <b>Статистика {self.period_title(days)}</b>", ""]

        if overview["wheel_posts"] > 0:
            lines.append(f"🎡 Публикаций с колёсами: <b>{overview['wheel_posts']}</b>")
            lines.append(f"📡 Источников с находками: <b>{overview['sources_with_wheels']}</b>")
            if overview["notifications"] > 0:
                lines.append(f"🔔 Отправлено уведомлений: <b>{overview['notifications']}</b>")
            if days > 1:
                average = overview["wheel_posts"] / days
                lines.append(f"📈 Среднее за день: <b>{average:.1f}</b>")
            if overview["top_sources"]:
                source, count = overview["top_sources"][0]
                lines.append(
                    f"🏆 Лидер периода: <b>@{html.escape(source)}</b> — {count}"
                )
        else:
            lines.append("За выбранный период новых публикаций с колёсами не найдено.")

        lines.extend(["", "<b>Сейчас</b>"])
        lines.append(f"🔥 Активных колёс: <b>{overview['active']}</b>")
        if overview["active"] > 0:
            lines.append(
                f"⏱ Время определено: <b>{overview['active_with_time']} из {overview['active']}</b>"
            )
            lines.append(
                f"✅ Участие отмечено: <b>{overview['participating']} из {overview['active']}</b>"
            )

        rows = [[
            {"text": "Сегодня", "callback_data": "page:stats:1"},
            {"text": "7 дней", "callback_data": "page:stats:7"},
            {"text": "30 дней", "callback_data": "page:stats:30"},
        ]]
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_reports(self) -> None:
        if not self.is_admin():
            self.send("Сводки доступны только администраторам.", reply_markup=self.with_nav())
            return
        rows = [
            [
                {"text": "Сегодня", "callback_data": "page:report:1"},
                {"text": "7 дней", "callback_data": "page:report:7"},
                {"text": "30 дней", "callback_data": "page:report:30"},
            ],
            [{"text": "📨 Отправить сводку", "callback_data": "summary:send"}],
        ]
        self.send(
            "📅 <b>Сводки</b>\n\n"
            "Посмотрите сводку в панели или отправьте её администраторам.",
            reply_markup=self.with_nav(rows),
        )

    def show_send_summary_menu(self) -> None:
        if not self.is_admin():
            self.send("Отправка сводок доступна только администраторам.", reply_markup=self.with_nav())
            return
        self.send(
            "📨 <b>Отправить сводку</b>\n\nВыберите период:",
            reply_markup=self.with_nav(self.summary_send_rows()),
        )

    def dispatch_summary(self, period: str) -> str:
        if period not in SUMMARY_PERIODS:
            raise ValueError("Неизвестный период сводки")
        self.dispatch("daily-report.yml", {"period": period})
        return SUMMARY_PERIODS[period][1]

    def show_period_report(self, days: int) -> None:
        if not self.is_admin():
            self.send("Сводки доступны только администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        lines = [f"📅 <b>Сводка {self.period_title(days)}</b>", ""]

        if overview["wheel_posts"] > 0:
            lines.extend(
                [
                    f"🎡 Публикаций с колёсами: <b>{overview['wheel_posts']}</b>",
                    f"📡 Источников с находками: <b>{overview['sources_with_wheels']}</b>",
                ]
            )
            if overview["notifications"] > 0:
                lines.append(f"🔔 Уведомлений отправлено: <b>{overview['notifications']}</b>")
            if days > 1:
                best_day, best_count = overview["best_day"]
                if best_day and best_count > 0:
                    try:
                        formatted = datetime.fromisoformat(best_day).strftime("%d.%m.%Y")
                    except ValueError:
                        formatted = best_day
                    lines.append(f"📈 Самый активный день: <b>{formatted}</b> — {best_count}")
            lines.extend(["", "<b>Лучшие источники</b>"])
            for index, (source, count) in enumerate(overview["top_sources"][:5], 1):
                lines.append(f"{index}. @{html.escape(source)} — {count}")
        else:
            lines.append("За этот период колёса не обнаружены.")

        lines.extend(["", "<b>Текущее состояние</b>"])
        lines.append(f"🔥 Активных колёс: <b>{overview['active']}</b>")
        if overview["active"] > 0:
            lines.append(
                f"⏱ С известным временем: <b>{overview['active_with_time']} из {overview['active']}</b>"
            )
            lines.append(
                f"✅ Участие отмечено: <b>{overview['participating']} из {overview['active']}</b>"
            )
        self.send("\n".join(lines), reply_markup=self.with_nav())

    def show_inactive_report(self) -> None:
        snap = self.snapshot(force=True)
        rows = self.source_sets(snap)["inactive"]
        stats = snap.stats.get("sources", {})
        lines = [f"📭 <b>Давно без колёс: {len(rows)}</b>", ""]
        now = datetime.now(timezone.utc)
        for source in rows[:25]:
            entry = stats.get(source, {}) if isinstance(stats.get(source), dict) else {}
            reference = self.parse_dt(
                entry.get("last_wheel_post_at") or entry.get("first_checked_at")
            )
            days = max(0, (now - reference.astimezone(timezone.utc)).days) if reference else 0
            detail = f"{days} дн." if reference else "нет истории"
            lines.append(f"• @{html.escape(source)} — {detail}")
        if not rows:
            lines.append("Все основные источники недавно публиковали колёса или ещё проходят наблюдение.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить", "callback_data": "page:report:inactive"}]]
            ),
        )

    def show_control(self) -> None:
        if not self.is_admin():
            self.send("Управление доступно только администраторам.", reply_markup=self.with_nav())
            return
        self.send(
            "🛠 <b>Управление</b>\n\n"
            "Здесь остаются только запуск проверки, состояние системы и диагностика. "
            "Отправка сводок находится в разделе «Сводки».",
            reply_markup=self.with_nav(self.control_menu_rows()),
        )

    def notification_preferences(self, user_id: str | None = None) -> dict[str, bool]:
        prefs = super().notification_preferences(user_id)
        target = str(user_id or self.current_user_id or "")
        if self.role_for(target) not in {"owner", "admin"}:
            prefs["monthly_reports"] = False
        return prefs

    def render_page(self, page: str) -> None:
        if page == "analytics":
            self.show_analytics()
            return
        if page == "reports":
            self.show_reports()
            return
        if page.startswith("report:"):
            value = page.split(":", 1)[1]
            if value == "inactive":
                self.show_inactive_report()
                return
            if value == "errors":
                self.send(
                    "Отдельная кнопка ошибок убрана. Текущее состояние источников видно в разделе «Источники».",
                    reply_markup=self.with_nav(),
                )
                return
            if value.isdigit():
                self.show_period_report(int(value))
                return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data == "summary:send" or data.startswith("summary:send:") or data == "control:daily":
            self._prepare_callback_user(query)
            query_id = str(query.get("id") or "")
            if not self.is_admin():
                self.answer(query_id, "Недоступно")
                return
            if data == "summary:send":
                self.answer(query_id, "Выберите период")
                self.show_send_summary_menu()
                return
            period = "daily" if data == "control:daily" else data.rsplit(":", 1)[1]
            label = self.dispatch_summary(period)
            self.answer(query_id, "Сводка запущена")
            self.send(
                f"📨 {html.escape(label)} сводка поставлена в очередь на отправку администраторам.",
                reply_markup=self.with_nav(),
            )
            return
        super().handle_callback(query)


def _callbacks(rows: list[list[dict[str, Any]]]) -> list[str]:
    return [str(button.get("callback_data") or "") for row in rows for button in row]


def self_test() -> None:
    admin_analytics = _callbacks(TelegramPanelRuntimeV31.analytics_menu_rows(True))
    user_analytics = _callbacks(TelegramPanelRuntimeV31.analytics_menu_rows(False))
    assert "page:reports" in admin_analytics
    assert "page:reports" not in user_analytics
    assert "page:report:inactive" in admin_analytics
    assert "page:report:inactive" in user_analytics

    control = _callbacks(TelegramPanelRuntimeV31.control_menu_rows())
    assert "control:daily" not in control
    assert control == ["control:monitor", "page:status", "page:diagnostic"]

    send_rows = _callbacks(TelegramPanelRuntimeV31.summary_send_rows())
    assert send_rows == [
        "summary:send:daily",
        "summary:send:weekly",
        "summary:send:monthly",
    ]

    panel = TelegramPanelRuntimeV31()
    today = datetime.now(DISPLAY_TZ).date().isoformat()
    snap = SimpleNamespace(
        stats={
            "daily": {
                today: {
                    "totals": {
                        "wheel_posts": 3,
                        "preliminary_sent": 2,
                        "activation_sent": 1,
                        "duplicates_suppressed": 99,
                        "errors": 0,
                    },
                    "sources": {
                        "official": {"wheel_posts": 2},
                        "collector": {"wheel_posts": 1},
                    },
                }
            }
        },
        state={
            "active_wheels": {
                "one": {"identifier": "one", "deadline": "2026-07-15T10:00:00+00:00"},
                "two": {"identifier": "two"},
            },
            "participating_wheels": {"one": {"marked_at": "now"}},
        },
    )
    overview = panel.period_overview(snap, 1)
    assert overview["wheel_posts"] == 3
    assert overview["notifications"] == 3
    assert overview["sources_with_wheels"] == 2
    assert overview["top_sources"][0] == ("official", 2)
    assert overview["active"] == 2
    assert overview["active_with_time"] == 1
    assert overview["participating"] == 1
    print("admin_panel_runtime_v31 summaries and analytics self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV31().run()


if __name__ == "__main__":
    raise SystemExit(main())
