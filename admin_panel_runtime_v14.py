from __future__ import annotations

import argparse
import html
from typing import Any

from admin_panel_runtime_v13 import TelegramPanelRuntimeV13
from admin_panel_runtime_v6 import INTELLIGENCE_PER_PAGE


class TelegramPanelRuntimeV14(TelegramPanelRuntimeV13):
    """Panel v14: compact inline navigation, message editing, and bulk source actions."""

    def __init__(self) -> None:
        super().__init__()
        self._edit_message_id: int | None = None
        self._remove_reply_keyboard_before_send = False

    # ---------- Compact single-message interface ----------
    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        if admin:
            return [
                [
                    {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                    {"text": "🔥 Активные колёса", "callback_data": "page:active"},
                ],
                [
                    {"text": "📡 Источники", "callback_data": "page:sources"},
                    {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
                ],
                [
                    {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                    {"text": "📱 Приложение", "callback_data": "page:app"},
                ],
                [{"text": "⋯ Ещё разделы", "callback_data": "page:more"}],
            ]
        return [
            [
                {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
            ],
            [
                {"text": "📡 Источники", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
            ],
            [
                {"text": "📅 Отчёты", "callback_data": "page:reports"},
                {"text": "📱 Приложение", "callback_data": "page:app"},
            ],
        ]

    def _hide_reply_keyboard(self) -> None:
        target = str(self.current_chat_id or "")
        if not target:
            return
        try:
            result = self.telegram_api(
                "sendMessage",
                {
                    "chat_id": target,
                    "text": "Компактная панель включена.",
                    "reply_markup": {"remove_keyboard": True},
                    "disable_notification": True,
                },
            )
            message_id = int((result.get("result") or {}).get("message_id") or 0)
            if message_id:
                try:
                    self.telegram_api("deleteMessage", {"chat_id": target, "message_id": message_id})
                except Exception:
                    pass
        except Exception as exc:
            print(f"WARNING remove reply keyboard: {type(exc).__name__}: {exc}")

    @staticmethod
    def _telegram_error_text(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        return str(getattr(response, "text", "") or exc)

    def send(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        target = str(chat_id or self.current_chat_id or "")
        if self._remove_reply_keyboard_before_send and self._edit_message_id is None:
            self._remove_reply_keyboard_before_send = False
            self._hide_reply_keyboard()

        if self._edit_message_id is not None and target == str(self.current_chat_id or ""):
            payload: dict[str, Any] = {
                "chat_id": target,
                "message_id": self._edit_message_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": reply_markup or {"inline_keyboard": []},
            }
            try:
                return self.telegram_api("editMessageText", payload)
            except Exception as exc:
                detail = self._telegram_error_text(exc).casefold()
                if "message is not modified" in detail:
                    return {"ok": True, "result": {"not_modified": True}}
                print(f"WARNING edit panel message: {type(exc).__name__}: {exc}")

        return super().send(text, reply_markup=reply_markup, chat_id=chat_id)

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        admin = role in {"owner", "admin"}
        title = "панель управления" if admin else "информационная панель"
        self.send(
            f"🎡 <b>BetBoom Monitor — {title}</b>\n\n"
            f"Ваш доступ: <b>{self.role_name(role)}</b>\n"
            "Панель работает в одном сообщении: кнопки ниже переключают разделы без создания новой переписки.",
            reply_markup={"inline_keyboard": self.compact_menu_rows(admin)},
        )

    def show_more(self) -> None:
        self.send(
            "⋯ <b>Дополнительные разделы</b>",
            reply_markup=self.with_nav([
                [
                    {"text": "🏆 Рейтинг каналов", "callback_data": "page:ranking"},
                    {"text": "📅 Отчёты", "callback_data": "page:reports"},
                ],
                [
                    {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                    {"text": "✅ Состояние системы", "callback_data": "page:status"},
                ],
            ]),
        )

    def render_page(self, page: str) -> None:
        if page == "more":
            self.show_more()
            return
        super().render_page(page)

    # ---------- Compact wording ----------
    @staticmethod
    def source_mode_name(mode: str) -> str:
        return {
            "primary": "Основная проверка",
            "reserve": "Ночное наблюдение",
            "paused": "Временно приостановлены",
            "quiet": "Давно без колёс",
            "fast": "Основная проверка",
            "nightly": "Ночное наблюдение",
        }.get(mode, mode)

    def show_source_detail(self, source: str) -> None:
        source = self.safe_source(source)
        snap = self.snapshot()
        stats = self.merged_source_stats(snap).get(source, {})
        health = snap.health.get("sources", {}).get(source, {})
        discovery = snap.discovery.get("sources", {}).get(source, {})
        primary_set = {value.casefold() for value in snap.fast}
        nightly_set = {value.casefold() for value in snap.nightly}
        mode = "Основная проверка" if source.casefold() in primary_set else (
            "Ночное наблюдение" if source.casefold() in nightly_set else "Не включён"
        )
        raw_status = str(health.get("status") or discovery.get("status") or "unknown")
        failure_reason = str(
            health.get("failure_reason") or health.get("last_error") or ""
        ).strip()
        wheels = self.counter(stats, "wheel_posts") or self.counter(discovery, "wheel_links_found")
        score = int(stats.get("quality_score", 0) or 0)
        reason_line = (
            f"Причина: {html.escape(failure_reason[:180])}\n" if failure_reason else ""
        )
        text = (
            f"📡 <b>@{html.escape(source)}</b>\n\n"
            f"Проверяется: <b>{mode}</b>\n"
            f"Состояние: {html.escape(self.source_status_name(raw_status))}\n"
            f"{reason_line}"
            f"Проверок: {self.counter(stats, 'checks')}\n"
            f"Постов с колёсами: {wheels}\n"
            f"Очки рейтинга: {score}\n"
            f"Последнее колесо: {self.fmt_dt(stats.get('last_wheel_post_at') or discovery.get('latest_wheel_at'))}\n"
            f"Последняя проверка: {self.fmt_dt(health.get('last_checked_at') or discovery.get('checked_at'))}"
        )
        rows: list[list[dict[str, Any]]] = [[{"text": "Открыть Telegram", "url": f"https://telegram.me/{source}"}]]
        if self.is_admin():
            move: list[dict[str, str]] = []
            if mode != "Основная проверка":
                move.append({"text": "⚡ В основные", "callback_data": f"source:move:fast:{source}"})
            if mode != "Ночное наблюдение":
                move.append({"text": "🌙 В ночное наблюдение", "callback_data": f"source:move:nightly:{source}"})
            if move:
                rows.append(move)
            if raw_status == "quarantined":
                rows.append([{"text": "▶️ Возобновить проверки", "callback_data": f"source:clearq:{source}"}])
            rows.append([{"text": "🗑 Удалить", "callback_data": f"source:removeask:{source}"}])
        self.send(text, reply_markup=self.with_nav(rows))

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot()
        participating = {
            str(key).casefold()
            for key, entry in snap.state.get("participating_wheels", {}).items()
            if isinstance(entry, dict)
        }
        if not items:
            self.send(
                "🔥 <b>Действующих колёс сейчас нет.</b>",
                reply_markup=self.with_nav([[{"text": "🔄 Обновить список", "callback_data": "refresh:active"}]]),
            )
            return

        lines = [f"🔥 <b>Действующие колёса: {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier)
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            participates = identifier.casefold() in participating or key.casefold() in participating
            lines.extend([
                f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                f"⏳ {html.escape(self.remaining(deadline) if deadline else 'время не определено')}",
                f"📡 @{html.escape(source)}",
                "✅ Участие отмечено" if participates else "❌ Участие не отмечено",
                "",
            ])
            row: list[dict[str, str]] = []
            url = str(item.get("url") or "")
            if url:
                row.append({"text": "🎡 Открыть колесо", "url": url})
            if not participates:
                row.append({"text": "✅ Я участвую", "callback_data": f"wheel:part:{key}"})
            if row:
                buttons.append(row)
            if self.is_admin():
                buttons.append([{"text": "🗑 Убрать из списка", "callback_data": f"wheel:removeask:{key}"}])
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "refresh:active"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    # ---------- Bulk intelligence actions ----------
    def bulk_intelligence_rows(self, category: str) -> tuple[list[dict[str, Any]], int]:
        rows = self.filtered_intelligence_rows(category)
        public_rows = [row for row in rows if row.get("public") is True]
        return public_rows, max(0, len(rows) - len(public_rows))

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
        lines = [
            f"🛰️ <b>{html.escape(titles.get(category, 'Результаты разведки'))}</b>",
            f"Страница {page + 1} из {max_page + 1}",
            "",
        ]
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

        bulk_rows, skipped = self.bulk_intelligence_rows(category)
        if category in {"new", "wheels"} and bulk_rows:
            buttons.extend([
                [{"text": f"⚡ Все в основные ({len(bulk_rows)})", "callback_data": f"intel:bulkask:fast:{category}"}],
                [{"text": f"🌙 Все в ночное наблюдение ({len(bulk_rows)})", "callback_data": f"intel:bulkask:nightly:{category}"}],
            ])
            if skipped:
                lines.append(f"\nНе подтверждены как публичные и не войдут в групповое действие: {skipped}.")
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    @staticmethod
    def _write_source_list(header: str, values: list[str]) -> str:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip().lstrip("@")
            key = value.casefold()
            if value and key not in seen:
                result.append(value)
                seen.add(key)
        return header.rstrip() + "\n\n" + "\n".join(result) + "\n"

    def bulk_set_intelligence_mode(self, category: str, mode: str) -> tuple[int, int]:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        if mode not in {"fast", "nightly"}:
            raise ValueError("Неизвестный режим")
        rows, skipped = self.bulk_intelligence_rows(category)
        targets = [str(row.get("source") or "").strip().lstrip("@") for row in rows]
        targets = [value for value in targets if value]
        if not targets:
            return 0, skipped

        fast_text, _ = self.get_file("public_sources.txt")
        nightly_text, _ = self.get_file("source_catalog.txt")
        fast = self.parse_list(fast_text)
        nightly = self.parse_list(nightly_text)
        target_keys = {value.casefold() for value in targets}
        fast = [value for value in fast if value.casefold() not in target_keys]
        nightly = [value for value in nightly if value.casefold() not in target_keys]
        if mode == "fast":
            fast.extend(targets)
        else:
            nightly.extend(targets)

        fast_new = self._write_source_list(
            "# Основная проверка: все ранее отобранные публичные Telegram-источники.\n"
            "# Проверяется с интервалом, выбранным в настройках Telegram-панели.\n"
            "# Автоматический перенос в ночную проверку возможен только после 7 полных дней наблюдения без новых колёс.",
            fast,
        )
        nightly_new = self._write_source_list(
            "# Ночное наблюдение: резервные источники и кандидаты.\n"
            "# Возврат в основную проверку выполняется администратором.",
            nightly,
        )
        if fast_new != fast_text:
            self.update_file("public_sources.txt", fast_new, f"Bulk move intelligence candidates to {mode} via Telegram")
        if nightly_new != nightly_text:
            self.update_file("source_catalog.txt", nightly_new, f"Bulk move intelligence candidates to {mode} via Telegram")
        self.cache = None
        self.dispatch("monitor.yml", {"continuous": "true"})
        if mode == "nightly":
            self.dispatch("nightly-discovery.yml", None)
        return len(targets), skipped

    # ---------- Handlers ----------
    def handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        command = text.split("@", 1)[0].split(maxsplit=1)[0].casefold() if text else ""
        legacy_buttons = {
            "📊 Статистика", "🔥 Активные колёса", "📡 Источники", "🏆 Рейтинг каналов",
            "📅 Отчёты", "🌙 Ночное наблюдение", "🛰️ Разведка источников", "⚙️ Настройки",
            "📱 Приложение", "✅ Проверка работы", "🛠 Управление", "🏠 Главное меню",
        }
        if command in {"/start", "/menu"} or text in legacy_buttons:
            self._remove_reply_keyboard_before_send = True
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        message = query.get("message") or {}
        message_id = int(message.get("message_id") or 0)
        self._edit_message_id = message_id or None
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        self.set_context(chat.get("id"), sender.get("id"))
        try:
            if data.startswith("intel:bulkask:"):
                _, _, mode, category = data.split(":", 3)
                if not self.is_admin():
                    raise PermissionError
                rows, skipped = self.bulk_intelligence_rows(category)
                mode_text = "основную проверку" if mode == "fast" else "ночное наблюдение"
                self.answer(query_id, "Нужно подтверждение")
                self.send(
                    f"Подтвердить перенос <b>{len(rows)}</b> публичных каналов в {mode_text}?"
                    + (f"\nНе подтверждено публичных: {skipped}." if skipped else ""),
                    reply_markup=self.with_nav([
                        [{"text": "Да, перенести все", "callback_data": f"intel:bulk:{mode}:{category}"}],
                        [{"text": "Отмена", "callback_data": f"page:intel_list:{category}:0"}],
                    ]),
                )
                return
            if data.startswith("intel:bulk:"):
                _, _, mode, category = data.split(":", 3)
                moved, skipped = self.bulk_set_intelligence_mode(category, mode)
                self.answer(query_id, "Готово")
                mode_text = "основную проверку" if mode == "fast" else "ночное наблюдение"
                self.refresh_snapshot()
                self.send(
                    f"✅ В {mode_text} перенесено: <b>{moved}</b>."
                    + (f"\nПропущено неподтверждённых публичных: {skipped}." if skipped else ""),
                    reply_markup=self.with_nav([[{"text": "🛰️ К разведке", "callback_data": "page:intelligence"}]]),
                )
                return
            super().handle_callback(query)
        except PermissionError:
            self.answer(query_id, "Недостаточно прав")
        except Exception as exc:
            self.answer(query_id, "Ошибка")
            self.send(f"⚠️ Ошибка: <code>{html.escape(type(exc).__name__)}</code>.", reply_markup=self.with_nav())
        finally:
            self._edit_message_id = None


def self_test() -> None:
    rows = TelegramPanelRuntimeV14.compact_menu_rows(True)
    flat = [button for row in rows for button in row]
    assert any(button.get("callback_data") == "page:active" for button in flat)
    assert any(button.get("callback_data") == "page:more" for button in flat)
    assert "прокрутка впереди" not in TelegramPanelRuntimeV14.show_active.__code__.co_consts
    assert any(
        isinstance(value, str) and "Участие отмечено" in value
        for value in TelegramPanelRuntimeV14.show_active.__code__.co_consts
    )
    assert TelegramPanelRuntimeV14.source_mode_name("nightly") == "Ночное наблюдение"
    print("admin_panel_runtime_v14 compact panel self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV14().run()


if __name__ == "__main__":
    raise SystemExit(main())
