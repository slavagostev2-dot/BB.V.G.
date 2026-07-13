from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
from datetime import datetime
from typing import Any

import admin_bot as legacy
import admin_panel_v2
from admin_panel_runtime_v2 import TelegramPanelRuntimeV2

UTC = admin_panel_v2.UTC
INTERVAL_OPTIONS = (1, 3, 5, 10, 15, 30)
SIGNATURE_FIELD = "access_signature"


class TelegramPanelRuntimeV3(TelegramPanelRuntimeV2):
    """Secure and streamlined Telegram control panel."""

    @staticmethod
    def _security_payload(value: dict[str, Any]) -> bytes:
        protected = {
            "owner_id": str(value.get("owner_id") or ""),
            "admins": sorted({str(x) for x in value.get("admins", []) if str(x)}),
            "blocked_users": sorted(
                {str(x) for x in value.get("blocked_users", []) if str(x)}
            ),
        }
        return json.dumps(
            protected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def _signature(cls, value: dict[str, Any]) -> str:
        secret = (legacy.BOT_TOKEN or "").encode("utf-8")
        if not secret:
            return ""
        return hmac.new(secret, cls._security_payload(value), hashlib.sha256).hexdigest()

    @staticmethod
    def _trusted_owner() -> str:
        return str(legacy.ADMIN_USER_ID or legacy.BOT_CHAT_ID or "")

    def normalize_access(self, value: dict[str, Any]) -> dict[str, Any]:
        result = super().normalize_access(value)
        raw_settings = value.get("settings") if isinstance(value, dict) else None
        interval = 5
        if isinstance(raw_settings, dict):
            try:
                interval = int(raw_settings.get("monitor_interval_minutes", 5))
            except (TypeError, ValueError):
                interval = 5
        result["settings"]["monitor_interval_minutes"] = (
            interval if interval in INTERVAL_OPTIONS else 5
        )

        supplied = str(value.get(SIGNATURE_FIELD) or "") if isinstance(value, dict) else ""
        expected = self._signature(result)
        trusted_owner = self._trusted_owner()
        valid = bool(supplied and expected and hmac.compare_digest(supplied, expected))
        if not valid:
            # Unsigned legacy configuration may keep only the trusted bootstrap owner.
            current_owner = str(result.get("owner_id") or "")
            result["owner_id"] = current_owner if current_owner == trusted_owner else trusted_owner
            result["admins"] = []
            result["blocked_users"] = []
        result[SIGNATURE_FIELD] = self._signature(result)
        result["version"] = 3
        return result

    def show_active(self) -> None:
        snap = self.snapshot()
        active = snap.state.get("active_wheels", {})
        pending = snap.state.get("pending_posts", {})
        participation = {
            str(key).casefold()
            for key, entry in snap.state.get("participating_wheels", {}).items()
            if isinstance(entry, dict)
        }

        rows: dict[str, dict[str, Any]] = {}
        for key, entry in active.items():
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item["_key"] = str(key)
            item["_state"] = "active"
            identity = str(item.get("identifier") or key).casefold()
            rows[identity] = item
        for entry in pending.values():
            if not isinstance(entry, dict):
                continue
            identity = str(entry.get("identifier") or entry.get("url") or "").casefold()
            if not identity:
                continue
            item = rows.get(identity, dict(entry))
            item.setdefault("_key", identity)
            item.setdefault("_state", "pending")
            rows[identity] = item

        ordered = sorted(
            rows.values(),
            key=lambda item: str(item.get("message_date") or item.get("first_seen_at") or ""),
            reverse=True,
        )
        if not ordered:
            self.send(
                "🔥 <b>Действующих и ожидающих колёс сейчас нет.</b>",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить", "callback_data": "page:active"}]]
                ),
            )
            return

        lines = [f"🔥 <b>Колёса: {len(ordered)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        for index, item in enumerate(ordered[:25], 1):
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = str(item.get("_key") or identifier)
            source = str(item.get("source") or "неизвестно")
            deadline = self.parse_dt(item.get("deadline"))
            remaining = self.remaining(deadline) if deadline else "время не определено"
            state_text = (
                "подтверждено"
                if item.get("_state") == "active"
                else "ожидает подтверждения"
            )
            participates = identifier.casefold() in participation or key.casefold() in participation
            lines.append(
                f"{index}. <code>{html.escape(identifier)}</code>\n"
                f"   {state_text}; {html.escape(remaining)}\n"
                f"   источник: @{html.escape(source)}\n"
                f"   участие: {'✅ отмечено' if participates else '❌ не отмечено'}"
            )
            row: list[dict[str, str]] = []
            url = str(item.get("url") or "")
            if url:
                row.append({"text": "🎡 Открыть", "url": url})
            if not participates:
                row.append(
                    {"text": "✅ Я участвую", "callback_data": f"wheel:part:{key}"}
                )
            if row:
                buttons.append(row)
            if self.is_admin():
                buttons.append(
                    [
                        {"text": "🔄 Проверить", "callback_data": f"wheel:check:{key}"},
                        {"text": "🗑 Убрать", "callback_data": f"wheel:removeask:{key}"},
                    ]
                )
        buttons.append([{"text": "🔄 Обновить список", "callback_data": "page:active"}])
        self.send("\n\n".join(lines), reply_markup=self.with_nav(buttons))

    def show_sources(self) -> None:
        snap = self.snapshot()
        groups = self.source_sets(snap)
        rows = [
            [
                {
                    "text": f"⚡ Основная проверка ({len(groups['fast'])})",
                    "callback_data": "source_list:primary:0",
                }
            ],
            [
                {
                    "text": f"🌙 Ночная проверка ({len(groups['nightly'])})",
                    "callback_data": "source_list:reserve:0",
                }
            ],
            [
                {
                    "text": f"⏸ Временно приостановлены ({len(groups['quarantine'])})",
                    "callback_data": "source_list:paused:0",
                }
            ],
        ]
        if self.is_admin():
            rows.append([{"text": "➕ Добавить источник", "callback_data": "source:add"}])
        self.send(
            "📡 <b>Источники</b>\n\n"
            "Здесь находятся списки каналов по режимам проверки. "
            "Отчёт «Давно без колёс» оставлен только во вкладке отчётов.",
            reply_markup=self.with_nav(rows),
        )

    def show_reports(self) -> None:
        rows = [
            [
                {"text": "Сегодня", "callback_data": "report:1"},
                {"text": "7 дней", "callback_data": "report:7"},
                {"text": "30 дней", "callback_data": "report:30"},
            ],
            [{"text": "📭 Давно без колёс", "callback_data": "report:inactive"}],
            [{"text": "⚠️ Ошибки источников", "callback_data": "report:errors"}],
        ]
        self.send(
            "📅 <b>Отчёты</b>\n\nОтправка ежедневного отчёта вручную находится только в разделе управления.",
            reply_markup=self.with_nav(rows),
        )

    def show_discovery(self) -> None:
        snap = self.snapshot()
        known = {str(name).casefold() for name in snap.discovery.get("sources", {})}
        nightly = list(snap.nightly)
        checked = sum(1 for name in nightly if name.casefold() in known)
        try:
            run = self.workflow_run("nightly-discovery.yml")
        except Exception:
            run = {}
        status = str(run.get("status") or "не запускался")
        status_text = {
            "queued": "🟡 ожидает запуска",
            "in_progress": "🔵 поиск выполняется",
            "completed": "🟢 последний поиск завершён",
        }.get(status, status)
        candidates = []
        for source, entry in snap.discovery.get("sources", {}).items():
            if not isinstance(entry, dict):
                continue
            found = self.counter(entry, "wheel_links_found")
            if found:
                candidates.append((str(source), found))
        candidates.sort(key=lambda item: (-item[1], item[0].casefold()))
        lines = [
            "🔎 <b>Поиск новых источников</b>",
            "",
            f"Состояние: {html.escape(status_text)}",
            f"База ночной проверки: {len(nightly)} каналов",
            f"Есть результаты проверки: {checked} из {len(nightly)}",
            f"Последнее завершение: {self.fmt_dt(snap.discovery.get('last_run_at'))}",
            "",
            "<b>Среди каких каналов идёт поиск</b>",
        ]
        lines.extend(f"• @{html.escape(name)}" for name in nightly[:30])
        if len(nightly) > 30:
            lines.append(f"• …и ещё {len(nightly) - 30}")
        lines.extend(["", "<b>Кандидаты, где находились колёса</b>"])
        lines.extend(
            f"• @{html.escape(name)} — найдено ссылок: {count}"
            for name, count in candidates[:15]
        )
        if not candidates:
            lines.append("• пока нет")
        action_rows = []
        if self.is_admin():
            action_rows.append(
                [{"text": "▶️ Запустить ночной поиск", "callback_data": "control:nightly"}]
            )
        self.send("\n".join(lines), reply_markup=self.with_nav(action_rows))

    def show_settings(self) -> None:
        if not self.is_admin():
            self.send("Настройки доступны только администраторам.", reply_markup=self.with_nav())
            return
        settings = self.load_access().get("settings", {})
        interval = int(settings.get("monitor_interval_minutes", 5))
        rows = [
            [{"text": f"Колёса {self.bool_mark(settings['wheel_notifications'])}", "callback_data": "setting:wheel_notifications"}],
            [{"text": f"Служебные сообщения {self.bool_mark(settings['service_notifications'])}", "callback_data": "setting:service_notifications"}],
            [
                {"text": f"Ежедневный {self.bool_mark(settings['daily_reports'])}", "callback_data": "setting:daily_reports"},
                {"text": f"Недельный {self.bool_mark(settings['weekly_reports'])}", "callback_data": "setting:weekly_reports"},
            ],
            [{"text": f"Панель пользователей {self.bool_mark(settings['public_panel'])}", "callback_data": "setting:public_panel"}],
            [{"text": "⏱ Интервал проверки", "callback_data": "page:interval"}],
            [{"text": "🔔 Получатели уведомлений", "callback_data": "page:recipients"}],
        ]
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
        self.send(
            "⚙️ <b>Настройки</b>\n\n"
            f"Интервал основной проверки: <b>{interval} мин.</b>\n"
            "Изменение интервала применяется после автоматического перезапуска монитора.",
            reply_markup=self.with_nav(rows),
        )

    def show_interval(self) -> None:
        if not self.is_admin():
            self.send("Недоступно.", reply_markup=self.with_nav())
            return
        current = int(self.load_access()["settings"].get("monitor_interval_minutes", 5))
        buttons = []
        for left, right in zip(INTERVAL_OPTIONS[::2], INTERVAL_OPTIONS[1::2]):
            buttons.append(
                [
                    {"text": f"{'✅ ' if left == current else ''}{left} мин.", "callback_data": f"interval:{left}"},
                    {"text": f"{'✅ ' if right == current else ''}{right} мин.", "callback_data": f"interval:{right}"},
                ]
            )
        self.send(
            "⏱ <b>Интервал проверки</b>\n\nВыберите, как часто проверять основные источники.",
            reply_markup=self.with_nav(buttons),
        )

    def set_interval(self, minutes: int) -> None:
        if not self.is_admin() or minutes not in INTERVAL_OPTIONS:
            raise PermissionError("Недоступное значение")
        access = self.load_access()
        access["settings"]["monitor_interval_minutes"] = minutes
        self.save_access("Update monitor interval via Telegram [skip ci]")
        self.dispatch("monitor.yml", {"continuous": "true"})

    def render_page(self, page: str) -> None:
        if page == "interval":
            self.show_interval()
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
        if data == "page:interval":
            self.answer(str(query.get("id") or ""), "Открываю")
            self.open_page("interval")
            return
        if data.startswith("interval:"):
            try:
                value = int(data.split(":", 1)[1])
                self.set_interval(value)
                self.answer(str(query.get("id") or ""), "Интервал изменён")
                self.show_interval()
            except Exception as exc:
                self.answer(str(query.get("id") or ""), "Ошибка")
                self.send(f"⚠️ Не удалось изменить интервал: {html.escape(type(exc).__name__)}")
            return
        super().handle_callback(query)


def self_test() -> None:
    bot = TelegramPanelRuntimeV3()
    unsigned = {
        "owner_id": bot._trusted_owner(),
        "admins": ["999"],
        "blocked_users": [],
        "settings": {"monitor_interval_minutes": 10},
    }
    normalized = bot.normalize_access(unsigned)
    assert normalized["admins"] == []
    assert normalized["settings"]["monitor_interval_minutes"] == 10
    signed = dict(normalized)
    signed[SIGNATURE_FIELD] = bot._signature(signed)
    assert bot.normalize_access(signed)["version"] == 3
    print("admin_panel_runtime_v3 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV3().run()


if __name__ == "__main__":
    raise SystemExit(main())
