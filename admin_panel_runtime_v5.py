from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from typing import Any

from admin_panel_runtime_v4 import ADMIN_KEYBOARD_V4, USER_KEYBOARD_V4, TelegramPanelRuntimeV4

UTC = timezone.utc
MODERATION_PATH = "candidate_moderation.json"
CANDIDATES_PER_PAGE = 5


class TelegramPanelRuntimeV5(TelegramPanelRuntimeV4):
    """Panel v5: moderated candidate queue for discovered Telegram sources."""

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
        latest = TelegramPanelRuntimeV5.parse_dt(entry.get("latest_wheel_at"))
        if latest:
            age_days = max(0, int((datetime.now(UTC) - latest.astimezone(UTC)).total_seconds() // 86400))
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
        moderation = self.load_moderation()
        ignored = moderation["ignored"]
        fast = {source.casefold() for source in snap.fast}
        nightly = {source.casefold() for source in snap.nightly}
        rows: list[dict[str, Any]] = []
        for source, raw in snap.discovery.get("sources", {}).items():
            if not isinstance(raw, dict):
                continue
            found = int(raw.get("wheel_links_found", 0) or 0)
            if found <= 0:
                continue
            key = str(source).casefold()
            if key in fast:
                category = "primary"
            elif key in ignored:
                category = "ignored"
            elif key in nightly:
                category = "nightly"
            else:
                category = "new"
            item = dict(raw)
            item["source"] = str(source)
            item["category"] = category
            item["score"] = self.candidate_score(item)
            rows.append(item)
        rows.sort(
            key=lambda item: (
                {"new": 0, "nightly": 1, "ignored": 2, "primary": 3}.get(str(item["category"]), 9),
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

    def show_discovery(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        snap = self.snapshot()
        rows = self.candidate_rows()
        new_count = sum(row["category"] == "new" for row in rows)
        nightly_count = sum(row["category"] == "nightly" for row in rows)
        ignored_count = sum(row["category"] == "ignored" for row in rows)
        strong_count = sum(row["category"] in {"new", "nightly"} and int(row["score"]) >= 70 for row in rows)
        try:
            run = self.workflow_run("nightly-discovery.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status in {"queued", "waiting", "pending"}:
            status_text = "🟡 ожидает запуска"
        elif status == "in_progress":
            status_text = "🔵 поиск выполняется"
        elif status == "completed" and conclusion == "success":
            status_text = "🟢 последний поиск завершён"
        elif conclusion:
            status_text = f"🔴 завершён с результатом: {conclusion}"
        else:
            status_text = "⚪ данных о запуске нет"
        discovery_keys = {str(value).casefold() for value in snap.discovery.get("sources", {})}
        checked = sum(1 for name in snap.nightly if name.casefold() in discovery_keys)
        text = (
            "🔎 <b>Кандидаты источников</b>\n\n"
            f"Состояние поиска: {html.escape(status_text)}\n"
            f"Последнее завершение: {self.fmt_dt(snap.discovery.get('last_run_at'))}\n"
            f"Проверено в последнем сохранённом запуске: {checked} из {len(snap.nightly)}\n\n"
            f"🆕 Требуют решения: <b>{new_count}</b>\n"
            f"🌙 Наблюдаются ночью: <b>{nightly_count}</b>\n"
            f"🟢 Сильных кандидатов: <b>{strong_count}</b>\n"
            f"🙈 Игнорируются: <b>{ignored_count}</b>\n\n"
            "Канал не переносится в основную проверку без решения администратора."
        )
        buttons = [
            [{"text": f"🆕 Требуют решения ({new_count})", "callback_data": "candidate:list:new:0"}],
            [{"text": f"🌙 Ночное наблюдение ({nightly_count})", "callback_data": "candidate:list:nightly:0"}],
            [{"text": f"🙈 Игнорируемые ({ignored_count})", "callback_data": "candidate:list:ignored:0"}],
            [{"text": "▶️ Запустить поиск сейчас", "callback_data": "control:nightly"}],
        ]
        self.send(text, reply_markup=self.with_nav(buttons))

    def show_candidate_list(self, category: str, page: int = 0) -> None:
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        rows = self._candidate_filter(category)
        max_page = max(0, (len(rows) - 1) // CANDIDATES_PER_PAGE)
        page = max(0, min(page, max_page))
        part = rows[page * CANDIDATES_PER_PAGE : (page + 1) * CANDIDATES_PER_PAGE]
        title = {
            "new": "Кандидаты, требующие решения",
            "nightly": "Кандидаты на ночном наблюдении",
            "ignored": "Игнорируемые кандидаты",
            "all": "Все кандидаты",
        }.get(category, "Кандидаты")
        lines = [f"🔎 <b>{html.escape(title)}</b>", f"Страница {page + 1} из {max_page + 1}", ""]
        buttons: list[list[dict[str, str]]] = []
        for item in part:
            source = str(item["source"])
            score = int(item["score"])
            found = int(item.get("wheel_links_found", 0) or 0)
            latest = self.fmt_dt(item.get("latest_wheel_at"))
            lines.extend([
                f"<b>@{html.escape(source)}</b>",
                f"{self.score_label(score)} · оценка {score}/100",
                f"Найдено колёс: {found} · последнее: {latest}",
                "",
            ])
            buttons.append([{
                "text": f"{self.score_label(score).split()[0]} @{source[:24]} · {score}",
                "callback_data": f"candidate:detail:{source}",
            }])
        if not part:
            lines.append("Список пуст.")
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "◀️", "callback_data": f"candidate:list:{category}:{page - 1}"})
        if page < max_page:
            nav.append({"text": "▶️", "callback_data": f"candidate:list:{category}:{page + 1}"})
        if nav:
            buttons.append(nav)
        buttons.append([{"text": "🔎 Сводка поиска", "callback_data": "page:discovery"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def _recent_candidate_wheels(self, source: str) -> list[dict[str, Any]]:
        snap = self.snapshot()
        result = []
        for entry in snap.discovery.get("notified_wheels", {}).values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("source") or "").casefold() != source.casefold():
                continue
            result.append(dict(entry))
        result.sort(key=lambda item: str(item.get("notified_at") or ""), reverse=True)
        return result[:5]

    def show_candidate_detail(self, source: str) -> None:
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        source = self.safe_source(source)
        item = next((row for row in self.candidate_rows() if str(row["source"]).casefold() == source.casefold()), None)
        if item is None:
            self.send(f"Кандидат @{html.escape(source)} больше не находится в очереди.", reply_markup=self.with_nav())
            return
        score = int(item["score"])
        category = str(item["category"])
        category_text = {
            "new": "требует решения",
            "nightly": "наблюдается ночью",
            "ignored": "игнорируется",
            "primary": "добавлен в основную проверку",
        }.get(category, category)
        lines = [
            f"📡 <b>@{html.escape(source)}</b>",
            "",
            f"Статус решения: <b>{html.escape(category_text)}</b>",
            f"Оценка: <b>{score}/100</b> — {self.score_label(score)}",
            f"Рекомендация: {html.escape(self.recommendation(score))}",
            "",
            f"Всего найдено ссылок: {int(item.get('wheel_links_found', 0) or 0)}",
            f"За последние 48 часов: {int(item.get('recent_wheel_links', 0) or 0)}",
            f"Подтверждено активных: {int(item.get('active_wheel_links', 0) or 0)}",
            f"Не подтверждено: {int(item.get('unconfirmed_wheel_links', 0) or 0)}",
            f"Просмотрено сообщений: {int(item.get('messages_checked', 0) or 0)}",
            f"Состояние канала: {html.escape(str(item.get('status') or 'нет данных'))}",
            f"Последнее колесо: {self.fmt_dt(item.get('latest_wheel_at'))}",
            f"Последняя проверка: {self.fmt_dt(item.get('checked_at'))}",
        ]
        recent_wheels = self._recent_candidate_wheels(source)
        if recent_wheels:
            lines.extend(["", "<b>Последние найденные колёса</b>"])
            for wheel in recent_wheels:
                identifier = str(wheel.get("identifier") or "колесо")
                lines.append(f"• <code>{html.escape(identifier)}</code> — {self.fmt_dt(wheel.get('notified_at'))}")
        buttons: list[list[dict[str, str]]] = [[{"text": "📨 Открыть канал", "url": f"https://telegram.me/{source}"}]]
        if category != "primary":
            buttons.append([{"text": "⚡ Добавить в основную", "callback_data": f"candidate:mode:fast:{source}"}])
        if category != "nightly":
            buttons.append([{"text": "🌙 Добавить в ночную", "callback_data": f"candidate:mode:nightly:{source}"}])
        if category == "ignored":
            buttons.append([{"text": "↩️ Вернуть в ночную проверку", "callback_data": f"candidate:restore:{source}"}])
        elif category != "primary":
            buttons.append([{"text": "🙈 Игнорировать", "callback_data": f"candidate:ignoreask:{source}"}])
        buttons.append([{"text": "🔎 К списку кандидатов", "callback_data": "page:discovery"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    def set_candidate_mode(self, source: str, mode: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        available, detail = self.verify_public_source(source)
        if not available:
            raise ValueError(detail)
        moderation = self.load_moderation()
        moderation["ignored"].pop(source.casefold(), None)
        self.save_moderation(moderation, f"Approve @{source} discovery candidate via Telegram [skip ci]")
        self.set_source_mode(source, mode)
        if mode == "nightly":
            return (
                f"@{source} добавлен в ночную проверку. "
                "Первая проверка пройдёт по ночному расписанию."
            )
        return f"@{source} добавлен в основную проверку."

    def ignore_candidate(self, source: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        self.set_source_mode(source, "remove")
        moderation = self.load_moderation()
        moderation["ignored"][source.casefold()] = {
            "source": source,
            "ignored_at": datetime.now(UTC).isoformat(),
            "ignored_by": "admin",
        }
        self.save_moderation(moderation, f"Ignore @{source} discovery candidate via Telegram [skip ci]")
        return f"@{source} исключён из поиска и скрыт из очереди."

    def restore_candidate(self, source: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        moderation = self.load_moderation()
        moderation["ignored"].pop(source.casefold(), None)
        self.save_moderation(moderation, f"Restore @{source} discovery candidate via Telegram [skip ci]")
        self.set_source_mode(source, "nightly")
        return (
            f"@{source} возвращён в ночную проверку. "
            "Следующая проверка пройдёт по ночному расписанию."
        )

    def render_page(self, page: str) -> None:
        if page.startswith("candidate_list:"):
            _, category, page_no = page.split(":", 2)
            self.show_candidate_list(category, int(page_no))
            return
        if page.startswith("candidate_detail:"):
            self.show_candidate_detail(page.split(":", 1)[1])
            return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = query.get("from") if isinstance(query, dict) else None
        self.set_context(
            chat.get("id") if isinstance(chat, dict) else None,
            sender.get("id") if isinstance(sender, dict) else None,
        )
        query_id = str(query.get("id") or "")
        try:
            if data.startswith("candidate:list:"):
                _, _, category, page_no = data.split(":", 3)
                self.answer(query_id, "Открываю")
                self.open_page(f"candidate_list:{category}:{page_no}")
                return
            if data.startswith("candidate:detail:"):
                source = data.split(":", 2)[2]
                self.answer(query_id, "Открываю")
                self.open_page(f"candidate_detail:{source}")
                return
            if data.startswith("candidate:mode:"):
                _, _, mode, source = data.split(":", 3)
                result = self.set_candidate_mode(source, mode)
                self.answer(query_id, "Готово")
                self.refresh_snapshot()
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("candidate:ignoreask:"):
                source = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Игнорировать @{html.escape(source)}?\n\n"
                    "Канал будет удалён из ночной проверки и перестанет появляться среди кандидатов.",
                    reply_markup=self.with_nav([[{
                        "text": "Да, игнорировать",
                        "callback_data": f"candidate:ignore:{source}",
                    }, {
                        "text": "Отмена",
                        "callback_data": f"candidate:detail:{source}",
                    }]]),
                )
                return
            if data.startswith("candidate:ignore:"):
                source = data.split(":", 2)[2]
                result = self.ignore_candidate(source)
                self.answer(query_id, "Скрыто")
                self.refresh_snapshot()
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("candidate:restore:"):
                source = data.split(":", 2)[2]
                result = self.restore_candidate(source)
                self.answer(query_id, "Возвращено")
                self.refresh_snapshot()
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
        except PermissionError:
            self.answer(query_id, "Недостаточно прав")
            return
        except Exception as exc:
            self.answer(query_id, "Ошибка")
            self.send(f"⚠️ Не удалось обработать кандидата: <code>{html.escape(type(exc).__name__)}</code>.")
            return
        super().handle_callback(query)


def self_test() -> None:
    bot = TelegramPanelRuntimeV5()
    assert bot.candidate_score({"wheel_links_found": 9}) >= 50
    assert bot.score_label(75).startswith("🟢")
    assert bot.recommendation(45) == "рекомендуется ночное наблюдение"
    assert len(ADMIN_KEYBOARD_V4["keyboard"]) == 5
    assert len(USER_KEYBOARD_V4["keyboard"]) == 3
    print("admin_panel_runtime_v5 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV5().run()


if __name__ == "__main__":
    raise SystemExit(main())
