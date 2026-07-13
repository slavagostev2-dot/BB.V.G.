from __future__ import annotations

import argparse
import html
import re
from datetime import datetime, timedelta
from typing import Any

import admin_bot as legacy
from admin_panel_runtime_v19 import BRAND_NAME, TelegramPanelRuntimeV19


UTC = legacy.UTC
HIDDEN_WHEEL_DAYS = 30
DEADLINE_GRACE_MINUTES = 30


class TelegramPanelRuntimeV20(TelegramPanelRuntimeV19):
    """Panel v20: no pending queue, manual deadlines and per-user hiding."""

    def _hidden_wheels(self, user_id: str | None = None) -> dict[str, dict[str, Any]]:
        target = str(user_id or self.current_user_id or "")
        access = self.load_access()
        record = access.get("users", {}).get(target)
        if not isinstance(record, dict):
            return {}
        raw = record.get("hidden_wheels")
        if isinstance(raw, list):
            return {str(value).casefold(): {} for value in raw if str(value)}
        if not isinstance(raw, dict):
            return {}
        now = datetime.now(UTC)
        result: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            normalized = str(key).casefold()
            if not normalized:
                continue
            entry = value if isinstance(value, dict) else {}
            expires = self.parse_dt(entry.get("expires_at"))
            if expires and expires.astimezone(UTC) <= now:
                continue
            result[normalized] = dict(entry)
        return result

    def hide_wheel_for_current_user(self, key: str) -> None:
        normalized = str(key or "").casefold()
        if not normalized or not self.current_user_id:
            raise ValueError("Колесо или пользователь не определены")
        access = self.load_access(force=True)
        users = access.setdefault("users", {})
        record = users.get(str(self.current_user_id))
        if not isinstance(record, dict):
            record = {
                "id": str(self.current_user_id),
                "chat_id": str(self.current_chat_id or self.current_user_id),
            }
            users[str(self.current_user_id)] = record
        hidden = self._hidden_wheels(str(self.current_user_id))
        now = datetime.now(UTC)
        hidden[normalized] = {
            "hidden_at": now.isoformat(),
            "expires_at": (now + timedelta(days=HIDDEN_WHEEL_DAYS)).isoformat(),
        }
        record["hidden_wheels"] = hidden
        self.save_access("Hide inactive wheel for Telegram user [skip ci]")

    def _collect_current_wheels(self) -> list[dict[str, Any]]:
        snap = self.snapshot()
        now = datetime.now(UTC)
        hidden = set(self._hidden_wheels())
        inactive = {
            str(key).casefold()
            for key, entry in snap.state.get("inactive_wheels", {}).items()
            if isinstance(entry, dict)
            and (
                self.parse_dt(entry.get("expires_at")) is None
                or self.parse_dt(entry.get("expires_at")).astimezone(UTC) > now
            )
        }
        result: list[dict[str, Any]] = []
        for key, raw in snap.state.get("active_wheels", {}).items():
            if not isinstance(raw, dict):
                continue
            normalized = str(key).casefold()
            identifier = str(raw.get("identifier") or key).casefold()
            if normalized in hidden or identifier in hidden:
                continue
            if normalized in inactive or identifier in inactive:
                continue
            deadline = self.parse_dt(raw.get("deadline"))
            if deadline and deadline.astimezone(UTC) < now - timedelta(minutes=DEADLINE_GRACE_MINUTES):
                continue
            item = dict(raw)
            item["_key"] = str(key)
            item["_live_state"] = "scheduled" if deadline else "manual_time_required"
            result.append(item)
        result.sort(
            key=lambda item: (
                self.parse_dt(item.get("deadline")) is None,
                self.parse_dt(item.get("deadline")) or datetime.max.replace(tzinfo=UTC),
                str(item.get("message_date") or ""),
            )
        )
        return result

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
                f"🔥 <b>{BRAND_NAME}: активных колёс сейчас нет.</b>",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить список", "callback_data": "refresh:active"}]]
                ),
            )
            return

        lines = [f"🔥 <b>{BRAND_NAME}: активные колёса — {len(items)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        admin = self.is_admin()
        for index, item in enumerate(items[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier).casefold()
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            joined = identifier.casefold() in participating or key in participating
            if deadline:
                time_text = self.remaining(deadline)
                status_text = "🟡 время прокрутки известно"
            else:
                time_text = "время не указано"
                status_text = "🟠 администратор может указать время вручную"
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier)}</code></b>",
                    status_text,
                    f"⏳ {html.escape(time_text)}",
                    f"📡 @{html.escape(source)}",
                    "✅ Участие отмечено" if joined else "❌ Участие не отмечено",
                    "",
                ]
            )

            url = str(item.get("url") or "")
            if url:
                buttons.append([{"text": f"🎡 Открыть {index}", "url": url}])
            actions: list[dict[str, str]] = []
            if not joined:
                actions.append({"text": "✅ Участвую", "callback_data": f"wheel:part:{key}"})
            actions.append({"text": "🚫 Неактивное", "callback_data": f"wheel:inactive:{key}"})
            buttons.append(actions)
            if admin and not deadline:
                buttons.append(
                    [{"text": "⏱ Указать время", "callback_data": f"wheel:time:{key}"}]
                )
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "refresh:active"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def show_stats(self, days: int = 1) -> None:
        snap = self.snapshot()
        totals = self.period_totals(snap.stats, days)
        title = "сегодня" if days == 1 else f"за {days} дней"
        text = (
            f"📊 <b>{BRAND_NAME}: статистика {title}</b>\n\n"
            f"Проверок источников: {totals.get('checks', 0)}\n"
            f"Просмотрено сообщений: {totals.get('messages_scanned', 0)}\n"
            f"Найдено постов с колёсами: {totals.get('wheel_posts', 0)}\n"
            f"Отправлено уведомлений: {totals.get('preliminary_sent', 0)}\n"
            f"Колёс с подтверждённым временем: {totals.get('activation_sent', 0)}\n"
            f"Повторы подавлены: {totals.get('duplicates_suppressed', 0)}\n"
            f"Ошибок проверки: {totals.get('errors', 0)}\n\n"
            f"Сейчас отображается колёс: {len(self._collect_current_wheels())}"
        )
        rows: list[list[dict[str, str]]] = [
            [
                {"text": "Сегодня", "callback_data": "page:stats:1"},
                {"text": "7 дней", "callback_data": "page:stats:7"},
                {"text": "30 дней", "callback_data": "page:stats:30"},
            ],
            [
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
                {"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"},
            ],
            [{"text": "⚠️ Ошибки источников", "callback_data": "page:report:errors"}],
        ]
        if self.is_admin():
            rows.append(
                [{"text": "📨 Отправить ежедневную сводку", "callback_data": "control:daily"}]
            )
        self.send(text, reply_markup=self.with_nav(rows))

    @staticmethod
    def parse_manual_deadline(text: str) -> datetime | None:
        raw = str(text or "").strip().casefold().replace("ё", "е")
        now_local = datetime.now(legacy.DISPLAY_TZ)

        parsed = TelegramPanelRuntimeV20.parse_dt(raw)
        if parsed:
            return parsed.astimezone(UTC)

        match = re.fullmatch(r"через\s+(\d{1,4})\s*(?:мин(?:ут[ыа]?)?|м)", raw)
        if match:
            return (now_local + timedelta(minutes=int(match.group(1)))).astimezone(UTC)
        match = re.fullmatch(r"через\s+(\d{1,3})\s*(?:час(?:а|ов)?|ч)", raw)
        if match:
            return (now_local + timedelta(hours=int(match.group(1)))).astimezone(UTC)
        match = re.fullmatch(
            r"(?:(\d{1,3})\s*(?:час(?:а|ов)?|ч))?\s*(?:(\d{1,3})\s*(?:мин(?:ут[ыа]?)?|м))?",
            raw,
        )
        if match and (match.group(1) or match.group(2)):
            return (
                now_local
                + timedelta(hours=int(match.group(1) or 0), minutes=int(match.group(2) or 0))
            ).astimezone(UTC)

        match = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", raw)
        if match:
            result = now_local.replace(
                hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
            )
            if result <= now_local + timedelta(minutes=2):
                result += timedelta(days=1)
            return result.astimezone(UTC)

        match = re.fullmatch(
            r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s+([01]?\d|2[0-3])[:.]([0-5]\d)",
            raw,
        )
        if match:
            day, month, year_text, hour, minute = match.groups()
            year = int(year_text) if year_text else now_local.year
            if year < 100:
                year += 2000
            try:
                result = datetime(
                    year,
                    int(month),
                    int(day),
                    int(hour),
                    int(minute),
                    tzinfo=legacy.DISPLAY_TZ,
                )
            except ValueError:
                return None
            return result.astimezone(UTC)
        return None

    def request_manual_time(self, key: str) -> None:
        if not self.is_admin():
            raise PermissionError("Только администратор может задавать время")
        self.pending_input[int(self.current_user_id or 0)] = {
            "kind": "wheel_time",
            "key": str(key).casefold(),
        }
        self.send(
            f"⏱ <b>Укажите время для <code>{html.escape(str(key))}</code></b>\n\n"
            "Поддерживаемые варианты:\n"
            "• <code>18:30</code> — ближайшее такое время по Барнаулу;\n"
            "• <code>14.07 18:30</code>;\n"
            "• <code>через 45 минут</code>;\n"
            "• <code>2 часа 15 минут</code>.\n\n"
            "Для отмены отправьте <code>/cancel</code>.",
            reply_markup=self.with_nav(),
        )

    def _delete_callback_message(self, query: dict[str, Any]) -> None:
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        if chat_id is None or message_id is None:
            return
        try:
            self.telegram_api(
                "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)}
            )
        except Exception as exc:
            print(f"WARNING delete hidden wheel message: {type(exc).__name__}: {exc}")

    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = message.get("from") if isinstance(message, dict) else None
        self.set_context(
            chat.get("id") if isinstance(chat, dict) else None,
            sender.get("id") if isinstance(sender, dict) else None,
        )
        pending = self.pending_input.get(int(self.current_user_id or 0))
        if isinstance(pending, dict) and pending.get("kind") == "wheel_time":
            self.current_role = self.role_for(self.current_user_id)
            if not self.is_admin():
                self.pending_input.pop(int(self.current_user_id or 0), None)
                self.send("Недостаточно прав.")
                return
            text = str(message.get("text") or "").strip()
            if text.casefold() == "/cancel":
                self.pending_input.pop(int(self.current_user_id or 0), None)
                self.send("Ввод времени отменён.", reply_markup=self.with_nav())
                return
            deadline = self.parse_manual_deadline(text)
            if deadline is None or deadline <= datetime.now(UTC):
                self.send("Не удалось распознать будущее время. Попробуйте один из примеров выше.")
                return
            key = str(pending.get("key") or "").casefold()
            self.dispatch_admin_action("set_deadline", f"{key}|{deadline.isoformat()}")
            self.pending_input.pop(int(self.current_user_id or 0), None)
            self.send(
                f"✅ Время для <code>{html.escape(key)}</code> принято: "
                f"<b>{deadline.astimezone(legacy.DISPLAY_TZ):%d.%m.%Y %H:%M}</b> по Барнаулу.\n"
                "Оно появится у всех пользователей после применения действия.",
                reply_markup=self.with_nav(),
            )
            return
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = query.get("from") if isinstance(query, dict) else None
        self.set_context(
            chat.get("id") if isinstance(chat, dict) else None,
            sender.get("id") if isinstance(sender, dict) else None,
        )
        data = str(query.get("data") or "")

        try:
            if data == "page:pending":
                self.answer(query_id, "Раздел удалён")
                self.show_stats(1)
                return
            if data.startswith("bb:t:") or data.startswith("wheel:time:"):
                if not self.is_admin():
                    raise PermissionError
                key = data.split(":", 2)[2].casefold()
                self.answer(query_id, "Жду время")
                self.request_manual_time(key)
                return
            if data.startswith("bb:x:") or data.startswith("wheel:inactive:"):
                key = data.split(":", 2)[2].casefold()
                if self.is_admin():
                    self.dispatch_admin_action(
                        "mark_inactive_global", f"{key}|{self.current_user_id or 'admin'}"
                    )
                    self.answer(query_id, "Удаляется для всех")
                    if data.startswith("bb:x:"):
                        self._delete_callback_message(query)
                    self.send(
                        f"🚫 Колесо <code>{html.escape(key)}</code> помечено неактивным. "
                        "Оно будет удалено у всех пользователей.",
                        reply_markup=self.with_nav(),
                    )
                else:
                    self.hide_wheel_for_current_user(key)
                    self.answer(query_id, "Скрыто только у вас")
                    if data.startswith("bb:x:"):
                        self._delete_callback_message(query)
                    else:
                        self.show_active()
                return
            if data.startswith("bb:p:"):
                token = data.split(":", 2)[2]
                self.dispatch_admin_action("participate_token", token)
                self.answer(query_id, "Участие отмечается")
                return
            if data.startswith("wheel:part:"):
                key = data.split(":", 2)[2]
                self.dispatch_admin_action("participate_wheel", key)
                self.answer(query_id, "Участие отмечается")
                return
            if data.startswith("bb:n:"):
                self.answer(query_id, "Участие уже отмечено")
                return
            super().handle_callback(query)
        except PermissionError:
            self.answer(query_id, "Доступно только администратору")
        except Exception as exc:
            print(f"ERROR BB V.G. callback {data}: {type(exc).__name__}: {exc}")
            self.answer(query_id, "Ошибка выполнения")
            self.send(
                f"⚠️ Не удалось выполнить действие: <code>{html.escape(type(exc).__name__)}</code>."
            )

    def render_page(self, page: str) -> None:
        if page == "pending":
            self.show_stats(1)
            return
        super().render_page(page)


def self_test() -> None:
    panel = TelegramPanelRuntimeV20()
    assert BRAND_NAME == "BB V.G."
    assert panel.parse_manual_deadline("через 45 минут") is not None
    assert panel.parse_manual_deadline("2 часа 15 минут") is not None
    assert "pending_posts" not in TelegramPanelRuntimeV20._collect_current_wheels.__code__.co_names
    assert "На перепроверке" not in TelegramPanelRuntimeV20.show_stats.__code__.co_consts
    print("admin_panel_runtime_v20 manual moderation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV20().run()


if __name__ == "__main__":
    raise SystemExit(main())
