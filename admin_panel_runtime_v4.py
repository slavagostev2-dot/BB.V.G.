from __future__ import annotations

import argparse
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import monitor
from admin_panel_runtime_v3 import TelegramPanelRuntimeV3

UTC = monitor.UTC

ADMIN_KEYBOARD_V4 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": "🔎 Поиск новых источников"}],
        [{"text": "✅ Проверка работы"}, {"text": "🛠 Управление"}],
        [{"text": "⚙️ Настройки"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Панель BetBoom Monitor",
}

USER_KEYBOARD_V4 = {
    "keyboard": [
        [{"text": "📊 Статистика"}, {"text": "🔥 Активные колёса"}],
        [{"text": "📡 Источники"}, {"text": "🏆 Рейтинг каналов"}],
        [{"text": "📅 Отчёты"}, {"text": "✅ Проверка работы"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "BetBoom Monitor",
}


class TelegramPanelRuntimeV4(TelegramPanelRuntimeV3):
    """Panel v4: complete menu and live-filtered wheel cards."""

    def show_menu(self, *, clear_stack: bool = True) -> None:
        if clear_stack:
            self.navigation[str(self.current_user_id or "guest")] = ["menu"]
        role = self.role_for(self.current_user_id)
        keyboard = ADMIN_KEYBOARD_V4 if role in {"owner", "admin"} else USER_KEYBOARD_V4
        title = "панель управления" if role in {"owner", "admin"} else "информационная панель"
        self.send(
            f"🎡 <b>BetBoom Monitor — {title}</b>\n\n"
            f"Ваш доступ: <b>{self.role_name(role)}</b>\n"
            "Все доступные разделы находятся на постоянной клавиатуре ниже.",
            reply_markup=keyboard,
        )

    @staticmethod
    def _entry_key(key: str, entry: dict[str, Any]) -> str:
        return str(entry.get("identifier") or key).casefold()

    def _inspect_entry(self, item: dict[str, Any]) -> tuple[str, Any]:
        url = str(item.get("url") or "")
        if not url:
            return "unknown", None
        try:
            inspection = monitor.inspect_wheel_page(url)
        except Exception:
            return "unknown", None
        return inspection.status, inspection

    def _collect_current_wheels(self) -> list[dict[str, Any]]:
        snap = self.snapshot()
        state = snap.state
        combined: dict[str, dict[str, Any]] = {}
        for key, raw in state.get("active_wheels", {}).items():
            if isinstance(raw, dict):
                item = dict(raw)
                item["_key"] = str(key)
                item["_stored_state"] = "active"
                combined[self._entry_key(str(key), item)] = item
        for raw in state.get("pending_posts", {}).values():
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            identity = self._entry_key(str(item.get("url") or ""), item)
            if not identity:
                continue
            item.setdefault("_key", identity)
            item.setdefault("_stored_state", "pending")
            combined.setdefault(identity, item)

        now = datetime.now(UTC)
        results: dict[str, tuple[str, Any]] = {}
        inspectable = {key: item for key, item in combined.items() if item.get("url")}
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(inspectable)))) as pool:
            futures = {pool.submit(self._inspect_entry, item): key for key, item in inspectable.items()}
            for future in as_completed(futures):
                results[futures[future]] = future.result()

        visible: list[dict[str, Any]] = []
        for identity, item in combined.items():
            deadline = self.parse_dt(item.get("deadline"))
            if deadline and deadline <= now:
                continue
            status, inspection = results.get(identity, ("unknown", None))
            if status == "inactive":
                continue
            if status == "active":
                item["_live_state"] = "active"
                if inspection and inspection.deadline:
                    item["deadline"] = inspection.deadline.isoformat()
            elif deadline and deadline > now:
                item["_live_state"] = "scheduled"
            else:
                first_seen = self.parse_dt(item.get("first_seen_at") or item.get("message_date"))
                # A page without an participation button is only shown briefly while the site may be updating.
                if not first_seen or now - first_seen > timedelta(minutes=30):
                    continue
                item["_live_state"] = "checking"
            visible.append(item)

        visible.sort(
            key=lambda item: (
                self.parse_dt(item.get("deadline")) is None,
                self.parse_dt(item.get("deadline")) or datetime.max.replace(tzinfo=UTC),
                str(item.get("message_date") or ""),
            )
        )
        return visible

    def show_active(self) -> None:
        snap = self.snapshot()
        items = self._collect_current_wheels()
        participating = {
            str(key).casefold()
            for key, value in snap.state.get("participating_wheels", {}).items()
            if isinstance(value, dict)
        }
        if not items:
            self.send(
                "🔥 <b>Действующих колёс сейчас нет.</b>\n\n"
                "Колёса без кнопки участия и с истёкшим временем в этот список не попадают.",
                reply_markup=self.with_nav([[{"text": "🔄 Обновить", "callback_data": "page:active"}]]),
            )
            return

        lines = [f"🔥 <b>Действующие колёса — {len(items)}</b>"]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier)
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            live_state = str(item.get("_live_state") or "checking")
            status_text = {
                "active": "🟢 участие открыто",
                "scheduled": "🟡 время подтверждено",
                "checking": "🟠 проверяем страницу",
            }.get(live_state, "🟠 проверяем страницу")
            joined = identifier.casefold() in participating or key.casefold() in participating
            time_text = self.remaining(deadline) if deadline else "время прокрутки не указано"
            lines.extend(
                [
                    "",
                    f"<b>{index}. {html.escape(identifier)}</b>",
                    status_text,
                    f"⏳ {html.escape(time_text)}",
                    f"📡 @{html.escape(source)}",
                    f"🙋 {'✅ участвую' if joined else '❌ не участвую'}",
                ]
            )
            row: list[dict[str, str]] = []
            url = str(item.get("url") or "")
            if url:
                row.append({"text": "🎡 Открыть колесо", "url": url})
            if not joined:
                row.append({"text": "✅ Отметить участие", "callback_data": f"wheel:part:{key}"})
            if row:
                buttons.append(row)
            if self.is_admin():
                buttons.append([
                    {"text": "🔄 Перепроверить", "callback_data": f"wheel:check:{key}"},
                    {"text": "🗑 Убрать", "callback_data": f"wheel:removeask:{key}"},
                ])
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "page:active"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))


def self_test() -> None:
    bot = TelegramPanelRuntimeV4()
    assert len(ADMIN_KEYBOARD_V4["keyboard"]) == 5
    assert "⚙️ Настройки" in str(ADMIN_KEYBOARD_V4)
    print("admin_panel_runtime_v4 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV4().run()


if __name__ == "__main__":
    raise SystemExit(main())
