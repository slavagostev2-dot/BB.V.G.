from __future__ import annotations

import argparse
import html
from typing import Any

import bot_private_state
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v21 import ADMIN_NOTIFICATION_OPTIONS, USER_NOTIFICATION_OPTIONS
from admin_panel_runtime_v29 import TelegramPanelRuntimeV29


class TelegramPanelRuntimeV30(TelegramPanelRuntimeV29):
    """Role-safe reports, source requests and notification defaults."""

    @staticmethod
    def analytics_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [[{"text": "📊 Статистика", "callback_data": "page:stats:1"}]]
        if admin:
            rows[0].append({"text": "📅 Отчёты", "callback_data": "page:reports"})
        return rows

    @staticmethod
    def source_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = [
            [
                {"text": "🔄 Обновить реестр", "callback_data": "page:sources"},
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
            ]
        ]
        if admin:
            rows.extend(
                [
                    [
                        {"text": "⚡ Основные источники", "callback_data": "source_list:primary:0"},
                        {"text": "🌙 Ночное наблюдение", "callback_data": "page:discovery"},
                    ],
                    [
                        {"text": "🛰️ Разведка источников", "callback_data": "page:intelligence"},
                        {"text": "➕ Добавить источник", "callback_data": "source:add"},
                    ],
                ]
            )
        else:
            rows.append(
                [
                    {"text": "📋 Основные источники", "callback_data": "source_list:primary:0"},
                    {"text": "➕ Предложить источник", "callback_data": "source:request"},
                ]
            )
        return rows

    @staticmethod
    def ranked_sources(stats: dict[str, Any]) -> list[tuple[str, int, int]]:
        source_rows = stats.get("sources") if isinstance(stats, dict) else None
        result: list[tuple[str, int, int]] = []
        if isinstance(source_rows, dict):
            for source, row in source_rows.items():
                if not isinstance(row, dict):
                    continue
                score = max(0, int(row.get("quality_score", 0) or 0))
                if score <= 0:
                    continue
                confirmed = max(0, int(row.get("admin_confirmed_wheels", 0) or 0))
                result.append((str(source), score, confirmed))
        result.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        return result[:10]

    def notification_preferences(self, user_id: str | None = None) -> dict[str, bool]:
        prefs = super().notification_preferences(user_id)
        target = str(user_id or self.current_user_id or "")
        if self.role_for(target) not in {"owner", "admin"}:
            prefs["daily_reports"] = False
            prefs["weekly_reports"] = False
            for key, _, _ in ADMIN_NOTIFICATION_OPTIONS:
                prefs[key] = False
        return prefs

    def register_user(self, message: dict[str, Any]) -> str:
        sender = message.get("from") if isinstance(message, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        user_id = str(sender.get("id") or "") if isinstance(sender, dict) else ""
        chat_id = str(chat.get("id") or user_id) if isinstance(chat, dict) else user_id
        access_before = self.load_access()
        users_before = access_before.get("users") if isinstance(access_before.get("users"), dict) else {}
        is_new = bool(user_id and user_id not in users_before)

        role = super().register_user(message)
        if not is_new or not user_id:
            return role

        access = self.load_access()
        users = access.setdefault("users", {})
        record = users.get(user_id)
        if not isinstance(record, dict):
            return role

        record["notifications_enabled"] = True
        prefs = self.notification_preferences(user_id)
        prefs["wheels"] = True
        if role in {"owner", "admin"}:
            for key, _, _ in (*USER_NOTIFICATION_OPTIONS, *ADMIN_NOTIFICATION_OPTIONS):
                prefs[key] = True
        else:
            prefs["daily_reports"] = False
            prefs["weekly_reports"] = False
            for key, _, _ in ADMIN_NOTIFICATION_OPTIONS:
                prefs[key] = False
        record["notification_preferences"] = prefs

        recipients = {str(value) for value in access.get("notification_recipients", []) if str(value)}
        if chat_id:
            recipients.add(chat_id)
        access["notification_recipients"] = sorted(recipients)
        self.save_access(f"Enable wheel notifications for new Telegram user {user_id} [skip ci]")
        return role

    def show_analytics(self) -> None:
        if self.is_admin():
            description = "Статистика и административные отчёты собраны в одном разделе."
        else:
            description = "Здесь доступна статистика. Создание и просмотр сводок доступны администраторам."
        self.send(
            f"📊 <b>Аналитика</b>\n\n{description}",
            reply_markup=self.with_nav(self.analytics_menu_rows(self.is_admin())),
        )

    def show_reports(self) -> None:
        if not self.is_admin():
            self.send("Отчёты и сводки доступны только администраторам.", reply_markup=self.with_nav())
            return
        super().show_reports()

    def show_ranking(self) -> None:
        rows = self.ranked_sources(self.snapshot().stats)
        lines = [
            "🏆 <b>Топ-10 источников</b>",
            "",
            "За подтверждённое администратором колесо источник получает +40 очков. "
            "Отметка «Неактивное» рейтинг не уменьшает.",
            "",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score, confirmed) in enumerate(rows, 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(
                f"{mark} <b>@{html.escape(source)}</b> — <b>{score}</b> оч. "
                f"({confirmed} подтвержд.)"
            )
        if not rows:
            lines.append("Пока нет источников с положительным рейтингом.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить рейтинг", "callback_data": "page:ranking"}]]
            ),
        )

    def show_notifications(self) -> None:
        prefs = self.notification_preferences()
        admin = self.is_admin()
        lines = [
            "🔔 <b>Уведомления</b>",
            "",
            "Выберите сообщения, которые хотите получать лично.",
            "",
        ]
        rows: list[list[dict[str, Any]]] = []
        user_options = USER_NOTIFICATION_OPTIONS if admin else USER_NOTIFICATION_OPTIONS[:1]
        for key, label, description in user_options:
            lines.append(f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}")
            rows.append(
                [{"text": f"{self.bool_mark(prefs[key])} {label}", "callback_data": f"notify:{key}"}]
            )
        if admin:
            lines.extend(["", "<b>Административные</b>"])
            for key, label, description in ADMIN_NOTIFICATION_OPTIONS:
                lines.append(f"{self.bool_mark(prefs[key])} {html.escape(label)} — {html.escape(description)}")
                rows.append(
                    [{"text": f"{self.bool_mark(prefs[key])} {label}", "callback_data": f"notify:{key}"}]
                )
        else:
            lines.extend(["", "Сводки и служебные уведомления доступны только администраторам."])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def toggle_notification(self, key: str) -> None:
        if not self.is_admin() and key != "wheels":
            raise PermissionError("Сводки доступны только администраторам")
        super().toggle_notification(key)

    def begin_source_request(self) -> None:
        if not self.current_user_id:
            raise PermissionError("Пользователь не определён")
        self.pending_input[int(self.current_user_id)] = {"kind": "source_request"}
        self.send(
            "➕ <b>Предложить источник</b>\n\n"
            "Отправьте username публичного Telegram-канала или ссылку на него.\n\n"
            "Например: <code>@channel_name</code>",
            reply_markup=self.with_nav(),
        )

    def render_page(self, page: str) -> None:
        if (page == "reports" or page.startswith("report:")) and not self.is_admin():
            self.send("Отчёты и сводки доступны только администраторам.", reply_markup=self.with_nav())
            return
        super().render_page(page)

    def handle_message(self, message: dict[str, Any]) -> None:
        if self.private_chat(message):
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            sender = message.get("from") if isinstance(message.get("from"), dict) else {}
            self.set_context(chat.get("id"), sender.get("id"))
            self.register_user(message)
            self.set_context(chat.get("id"), sender.get("id"))
            text = str(message.get("text") or "").strip()
            user_id = int(sender.get("id") or 0)
            pending = self.pending_input.get(user_id) if user_id else None
            if (
                text
                and isinstance(pending, dict)
                and str(pending.get("kind") or "") == "source_request"
                and not text.casefold().startswith(("/start", "/menu"))
            ):
                self.pending_input.pop(user_id, None)
                self.submit_source_request(text, message)
                return
        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data == "source:request":
            self._prepare_callback_user(query)
            if self.current_role == "blocked" or not self.can_view():
                self.answer(str(query.get("id") or ""), "Недоступно")
                return
            self.answer(str(query.get("id") or ""), "Жду username канала")
            self.begin_source_request()
            return
        super().handle_callback(query)


def _callbacks(rows: list[list[dict[str, Any]]]) -> list[str]:
    return [str(button.get("callback_data") or "") for row in rows for button in row]


def self_test() -> None:
    admin_analytics = _callbacks(TelegramPanelRuntimeV30.analytics_menu_rows(True))
    user_analytics = _callbacks(TelegramPanelRuntimeV30.analytics_menu_rows(False))
    assert "page:reports" in admin_analytics
    assert "page:reports" not in user_analytics

    admin_sources = _callbacks(TelegramPanelRuntimeV30.source_menu_rows(True))
    user_sources = _callbacks(TelegramPanelRuntimeV30.source_menu_rows(False))
    assert "source:request" in user_sources
    assert "source:request" not in admin_sources
    assert not {"page:discovery", "page:intelligence", "source:add"} & set(user_sources)

    stats = {
        "sources": {
            **{f"positive_{index}": {"quality_score": index * 40, "admin_confirmed_wheels": index} for index in range(1, 13)},
            "zero": {"quality_score": 0, "admin_rejected_wheels": 5},
        }
    }
    ranking = TelegramPanelRuntimeV30.ranked_sources(stats)
    assert len(ranking) == 10
    assert all(score > 0 for _, score, _ in ranking)
    assert ranking[0][0] == "positive_12"
    assert not any(source == "zero" for source, _, _ in ranking)

    panel = TelegramPanelRuntimeV30()
    access = panel._bootstrap_access(
        {
            "owner_id": "1",
            "users": {
                "1": {
                    "id": "1",
                    "chat_id": "1",
                    "first_name": "Owner",
                    "notifications_enabled": True,
                }
            },
        }
    )
    panel._bot_bundle = bot_private_state.default_bundle(access, default_source_requests())
    panel._save_bot_bundle = lambda message: True  # type: ignore[method-assign]
    panel.send = lambda *args, **kwargs: {"ok": True}  # type: ignore[method-assign]
    role = panel.register_user(
        {
            "chat": {"id": 2, "type": "private"},
            "from": {"id": 2, "username": "new_user", "first_name": "New"},
        }
    )
    assert role == "user"
    record = panel.access["users"]["2"]
    assert record["notifications_enabled"] is True
    assert record["notification_preferences"]["wheels"] is True
    assert record["notification_preferences"]["daily_reports"] is False
    assert "2" in {str(value) for value in panel.access["notification_recipients"]}
    print("admin_panel_runtime_v30 reports, requests, ranking and defaults self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV30().run()


if __name__ == "__main__":
    raise SystemExit(main())
