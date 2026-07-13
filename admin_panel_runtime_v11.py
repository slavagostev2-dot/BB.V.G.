from __future__ import annotations

import argparse
import html
from typing import Any

from admin_panel_runtime_v10 import TelegramPanelRuntimeV10
from admin_panel_runtime_v5 import CANDIDATES_PER_PAGE

DEPLOYMENT_PATH = "miniapp_deployment.json"
CLOUDFLARE_APP_URL = "https://slavagostev2-betboom-monitor.pages.dev/"
FALLBACK_APP_URL = (
    "https://raw.githack.com/slavagostev2-dot/"
    "betboom-wheel-monitor/main/docs/index.html"
)


class TelegramPanelRuntimeV11(TelegramPanelRuntimeV10):
    """Panel v11: clear nightly metrics and Cloudflare-ready Mini App."""

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

        discovery_keys = {str(value).casefold() for value in snap.discovery.get("sources", {})}
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
            [{"text": f"🆕 Требуют решения ({len(new_rows)})", "callback_data": "candidate:list:new:0"}],
            [{"text": f"🎡 С колёсами в ночной базе ({len(nightly_with_wheels)})", "callback_data": "candidate:list:nightly:0"}],
            [{"text": f"🙈 Игнорируемые ({len(ignored_rows)})", "callback_data": "candidate:list:ignored:0"}],
            [{"text": "▶️ Запустить ночную проверку", "callback_data": "control:nightly"}],
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
        part = rows[page * CANDIDATES_PER_PAGE:(page + 1) * CANDIDATES_PER_PAGE]
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
            lines.extend([
                f"<b>@{html.escape(source)}</b>",
                f"{self.score_label(score)} · оценка {score}/100",
                f"Найдено колёс: {found} · последнее: {self.fmt_dt(item.get('latest_wheel_at'))}",
                "",
            ])
            buttons.append([{
                "text": f"@{source[:24]} · колёс {found}",
                "callback_data": f"candidate:detail:{source}",
            }])
        if not part:
            lines.append("В ночной базе пока нет каналов с найденными колёсами.")
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "◀️", "callback_data": f"candidate:list:nightly:{page - 1}"})
        if page < max_page:
            nav.append({"text": "▶️", "callback_data": f"candidate:list:nightly:{page + 1}"})
        if nav:
            buttons.append(nav)
        buttons.append([{"text": "🌙 К сводке ночного наблюдения", "callback_data": "page:discovery"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))


def self_test() -> None:
    assert CLOUDFLARE_APP_URL.endswith(".pages.dev/")
    assert FALLBACK_APP_URL.startswith("https://raw.githack.com/")
    assert DEPLOYMENT_PATH == "miniapp_deployment.json"
    print("admin_panel_runtime_v11 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV11().run()


if __name__ == "__main__":
    raise SystemExit(main())
