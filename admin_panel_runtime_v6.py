from __future__ import annotations

import argparse
import html
from typing import Any

from admin_panel_runtime_v5 import TelegramPanelRuntimeV5

INTELLIGENCE_PATH = "intelligence_state.json"
INTELLIGENCE_PER_PAGE = 6
BTN_NIGHTLY = "🌙 Ночное наблюдение"
BTN_INTELLIGENCE = "🛰️ Разведка источников"

ADMIN_KEYBOARD_V6 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": BTN_NIGHTLY}],
        [{"text": BTN_INTELLIGENCE}, {"text": "✅ Проверка работы"}],
        [{"text": "🛠 Управление"}, {"text": "⚙️ Настройки"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Панель BetBoom Monitor",
}

USER_KEYBOARD_V6 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": "✅ Проверка работы"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "BetBoom Monitor",
}


class TelegramPanelRuntimeV6(TelegramPanelRuntimeV5):
    """Panel v6: separate nightly observation and real Telegram source intelligence."""

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        keyboard = ADMIN_KEYBOARD_V6 if role in {"owner", "admin"} else USER_KEYBOARD_V6
        title = "панель управления" if role in {"owner", "admin"} else "информационная панель"
        self.send(
            f"🎡 <b>BetBoom Monitor — {title}</b>\n\n"
            f"Ваш доступ: <b>{self.role_name(role)}</b>\n"
            "Ночное наблюдение проверяет известный резервный список. "
            "Разведка ищет ранее неизвестные каналы по связям внутри Telegram.",
            reply_markup=keyboard,
        )

    def intelligence_state(self) -> dict[str, Any]:
        value = self.get_json_file(INTELLIGENCE_PATH, {
            "version": 1,
            "candidates": {},
            "edges": {},
            "runs": [],
        })
        return value if isinstance(value, dict) else {}

    def intelligence_rows(self) -> list[dict[str, Any]]:
        state = self.intelligence_state()
        moderation = self.load_moderation()
        ignored = moderation.get("ignored", {})
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
            if folded in known:
                item["decision"] = "known"
            elif folded in ignored:
                item["decision"] = "ignored"
            else:
                item["decision"] = "new"
            rows.append(item)
        rows.sort(key=lambda item: (
            {"new": 0, "ignored": 1, "known": 2}.get(str(item.get("decision")), 9),
            -int(item.get("score", 0) or 0),
            -int(item.get("wheel_links_found", 0) or 0),
            str(item.get("source") or "").casefold(),
        ))
        return rows

    @staticmethod
    def intelligence_label(score: int, wheels: int) -> str:
        if wheels > 0 and score >= 60:
            return "🟢 подтверждённый"
        if score >= 35:
            return "🟡 связанный"
        return "⚪ слабый сигнал"

    def show_intelligence(self) -> None:
        if not self.is_admin():
            self.send("Этот раздел доступен администраторам.", reply_markup=self.with_nav())
            return
        state = self.intelligence_state()
        summary = state.get("last_run_summary", {}) if isinstance(state.get("last_run_summary"), dict) else {}
        rows = self.intelligence_rows()
        new_rows = [row for row in rows if row.get("decision") == "new"]
        wheel_rows = [row for row in new_rows if int(row.get("wheel_links_found", 0) or 0) > 0]
        try:
            run = self.workflow_run("source-intelligence.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status == "in_progress":
            status_text = "🔵 разведка выполняется"
        elif status in {"queued", "waiting", "pending"}:
            status_text = "🟡 ожидает запуска"
        elif status == "completed" and conclusion == "success":
            status_text = "🟢 последний запуск завершён"
        elif conclusion:
            status_text = f"🔴 ошибка: {conclusion}"
        else:
            status_text = "⚪ ещё не запускалась"
        text = (
            "🛰️ <b>Разведка новых источников</b>\n\n"
            f"Состояние: {html.escape(status_text)}\n"
            f"Последний запуск: {self.fmt_dt(state.get('last_run_at'))}\n\n"
            f"Известных каналов использовано как точки входа: {int(summary.get('known_sources', 0) or 0)}\n"
            f"Просканировано каналов: {int(summary.get('sources_scanned', 0) or 0)}\n"
            f"Найдено упоминаний и ссылок: {int(summary.get('references_found', 0) or 0)}\n"
            f"Уникальных неизвестных каналов: {int(summary.get('unique_candidates', 0) or 0)}\n"
            f"Проверено публичных страниц: {int(summary.get('verified_candidates', 0) or 0)}\n\n"
            f"🆕 Требуют решения: <b>{len(new_rows)}</b>\n"
            f"🎡 С найденными колёсами: <b>{len(wheel_rows)}</b>\n\n"
            "Разведка ищет каналы через @упоминания и ссылки t.me в публикациях уже известных источников."
        )
        buttons = [
            [{"text": f"🆕 Новые находки ({len(new_rows)})", "callback_data": "intel:list:new:0"}],
            [{"text": f"🎡 С колёсами ({len(wheel_rows)})", "callback_data": "intel:list:wheels:0"}],
            [{"text": "▶️ Запустить разведку сейчас", "callback_data": "control:intelligence"}],
            [{"text": "🌙 Открыть ночное наблюдение", "callback_data": "page:discovery"}],
        ]
        self.send(text, reply_markup=self.with_nav(buttons))

    def filtered_intelligence_rows(self, category: str) -> list[dict[str, Any]]:
        rows = self.intelligence_rows()
        if category == "wheels":
            return [row for row in rows if row.get("decision") == "new" and int(row.get("wheel_links_found", 0) or 0) > 0]
        if category == "new":
            return [row for row in rows if row.get("decision") == "new"]
        if category == "ignored":
            return [row for row in rows if row.get("decision") == "ignored"]
        return rows

    def show_intelligence_list(self, category: str, page: int = 0) -> None:
        rows = self.filtered_intelligence_rows(category)
        max_page = max(0, (len(rows) - 1) // INTELLIGENCE_PER_PAGE)
        page = max(0, min(page, max_page))
        part = rows[page * INTELLIGENCE_PER_PAGE:(page + 1) * INTELLIGENCE_PER_PAGE]
        titles = {
            "new": "Новые источники из Telegram-сети",
            "wheels": "Новые источники с найденными колёсами",
            "ignored": "Игнорируемые находки",
            "all": "Все результаты разведки",
        }
        lines = [f"🛰️ <b>{html.escape(titles.get(category, 'Результаты разведки'))}</b>", f"Страница {page + 1} из {max_page + 1}", ""]
        buttons: list[list[dict[str, str]]] = []
        for item in part:
            source = str(item.get("source") or "")
            score = int(item.get("score", 0) or 0)
            wheels = int(item.get("wheel_links_found", 0) or 0)
            refs = len(item.get("discovered_from", [])) if isinstance(item.get("discovered_from"), list) else 0
            lines.extend([
                f"<b>@{html.escape(source)}</b>",
                f"{self.intelligence_label(score, wheels)} · оценка {score}/100",
                f"Связей: {refs} · упоминаний: {int(item.get('mention_count', 0) or 0)} · колёс: {wheels}",
                "",
            ])
            buttons.append([{"text": f"@{source[:25]} · {score}", "callback_data": f"intel:detail:{source}"}])
        if not part:
            lines.append("Список пуст.")
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "◀️", "callback_data": f"intel:list:{category}:{page - 1}"})
        if page < max_page:
            nav.append({"text": "▶️", "callback_data": f"intel:list:{category}:{page + 1}"})
        if nav:
            buttons.append(nav)
        buttons.append([{"text": "🛰️ К сводке разведки", "callback_data": "page:intelligence"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def show_intelligence_detail(self, source: str) -> None:
        source = self.safe_source(source)
        item = next((row for row in self.intelligence_rows() if str(row.get("source") or "").casefold() == source.casefold()), None)
        if item is None:
            self.send("Результат разведки больше не найден.", reply_markup=self.with_nav())
            return
        score = int(item.get("score", 0) or 0)
        wheels = int(item.get("wheel_links_found", 0) or 0)
        discovered_from = item.get("discovered_from", []) if isinstance(item.get("discovered_from"), list) else []
        lines = [
            f"🛰️ <b>@{html.escape(source)}</b>", "",
            f"Оценка: <b>{score}/100</b> — {self.intelligence_label(score, wheels)}",
            f"Публичный канал: {'✅ да' if item.get('public') else '❌ не подтверждён'}",
            f"Найдено упоминаний: {int(item.get('mention_count', 0) or 0)}",
            f"Найдено колёс: {wheels}",
            f"Просмотрено сообщений при проверке: {int(item.get('messages_checked', 0) or 0)}",
            f"Последнее найденное колесо: {self.fmt_dt(item.get('latest_wheel_at'))}",
            f"Последняя проверка: {self.fmt_dt(item.get('last_verified_at'))}",
            "", "<b>Откуда найден</b>",
        ]
        lines.extend(f"• @{html.escape(name)}" for name in discovered_from[:12])
        if not discovered_from:
            lines.append("• источник связи не сохранён")
        samples = item.get("sample_wheels", []) if isinstance(item.get("sample_wheels"), list) else []
        if samples:
            lines.extend(["", "<b>Примеры колёс</b>"])
            for sample in samples[:5]:
                if isinstance(sample, dict):
                    lines.append(f"• <code>{html.escape(str(sample.get('identifier') or 'колесо'))}</code> — {self.fmt_dt(sample.get('published_at'))}")
        buttons: list[list[dict[str, str]]] = [[{"text": "📨 Открыть канал", "url": f"https://t.me/{source}"}]]
        if item.get("decision") != "known":
            buttons.append([{"text": "⚡ В основную проверку", "callback_data": f"intel:mode:fast:{source}"}])
            buttons.append([{"text": "🌙 В ночное наблюдение", "callback_data": f"intel:mode:nightly:{source}"}])
        if item.get("decision") == "ignored":
            buttons.append([{"text": "↩️ Вернуть в ночное наблюдение", "callback_data": f"intel:restore:{source}"}])
        elif item.get("decision") != "known":
            buttons.append([{"text": "🙈 Игнорировать", "callback_data": f"intel:ignoreask:{source}"}])
        buttons.append([{"text": "🛰️ К результатам", "callback_data": "page:intelligence"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    def render_page(self, page: str) -> None:
        if page == "intelligence":
            self.show_intelligence()
            return
        if page.startswith("intel_list:"):
            _, category, page_no = page.split(":", 2)
            self.show_intelligence_list(category, int(page_no))
            return
        if page.startswith("intel_detail:"):
            self.show_intelligence_detail(page.split(":", 1)[1])
            return
        super().render_page(page)

    def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        if text in {BTN_NIGHTLY, BTN_INTELLIGENCE}:
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            self.set_context(chat.get("id"), sender.get("id"))
            self.navigation[str(self.current_user_id)] = ["menu"]
            self.open_page("discovery" if text == BTN_NIGHTLY else "intelligence")
            return
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        self.set_context(chat.get("id"), sender.get("id"))
        query_id = str(query.get("id") or "")
        try:
            if data.startswith("intel:list:"):
                _, _, category, page_no = data.split(":", 3)
                self.answer(query_id, "Открываю")
                self.open_page(f"intel_list:{category}:{page_no}")
                return
            if data.startswith("intel:detail:"):
                source = data.split(":", 2)[2]
                self.answer(query_id, "Открываю")
                self.open_page(f"intel_detail:{source}")
                return
            if data.startswith("intel:mode:"):
                _, _, mode, source = data.split(":", 3)
                result = self.set_candidate_mode(source, mode)
                self.answer(query_id, "Добавлено")
                self.refresh_snapshot()
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("intel:ignoreask:"):
                source = data.split(":", 2)[2]
                self.answer(query_id, "Подтвердите")
                self.send(
                    f"Игнорировать @{html.escape(source)}? Канал будет исключён из дальнейшей разведки.",
                    reply_markup=self.with_nav([[
                        {"text": "Да, игнорировать", "callback_data": f"intel:ignore:{source}"},
                        {"text": "Отмена", "callback_data": f"intel:detail:{source}"},
                    ]]),
                )
                return
            if data.startswith("intel:ignore:"):
                source = data.split(":", 2)[2]
                result = self.ignore_candidate(source)
                self.answer(query_id, "Скрыто")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data.startswith("intel:restore:"):
                source = data.split(":", 2)[2]
                result = self.restore_candidate(source)
                self.answer(query_id, "Возвращено")
                self.send(f"✅ {html.escape(result)}", reply_markup=self.with_nav())
                return
            if data == "control:intelligence":
                if not self.is_admin():
                    raise PermissionError
                self.dispatch("source-intelligence.yml", None)
                self.answer(query_id, "Разведка запущена")
                self.send("▶️ Разведка новых источников запущена.", reply_markup=self.with_nav())
                return
        except PermissionError:
            self.answer(query_id, "Недостаточно прав")
            return
        except Exception as exc:
            self.answer(query_id, "Ошибка")
            self.send(f"⚠️ Ошибка разведки: <code>{html.escape(type(exc).__name__)}</code>.")
            return
        super().handle_callback(query)


def self_test() -> None:
    bot = TelegramPanelRuntimeV6()
    assert len(ADMIN_KEYBOARD_V6["keyboard"]) == 5
    assert BTN_INTELLIGENCE in str(ADMIN_KEYBOARD_V6)
    assert bot.intelligence_label(70, 2).startswith("🟢")
    print("admin_panel_runtime_v6 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV6().run()


if __name__ == "__main__":
    raise SystemExit(main())
