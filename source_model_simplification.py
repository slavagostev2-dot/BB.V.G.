from __future__ import annotations

import copy
import html
from typing import Any, Callable


REMOVED_PAGE_PREFIXES = (
    "analytics",
    "stats:",
    "reports",
    "report:inactive",
    "intelligence",
    "intel_list:",
    "intel_detail:",
    "discovery",
)
REMOVED_CALLBACK_PREFIXES = (
    "page:analytics",
    "page:stats:",
    "page:reports",
    "page:report:inactive",
    "page:intelligence",
    "page:intel_list:",
    "intel:list:",
    "intel:detail:",
    "intel:bulk",
    "control:intelligence",
    "control:nightly",
    "control:daily",
    "summary:send:",
)
SUMMARY_NOTIFICATION_KEYS = {
    "daily_reports",
    "weekly_reports",
    "monthly_reports",
}
SOURCE_HEADER = (
    "# Единая база источников BetBoom Monitor.\n"
    "# Все каналы проверяются одним основным монитором с интервалом из настроек Telegram-панели."
)


def _unique_sources(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip().lstrip("@")
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _source_entry(mapping: object, source: str) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    target = source.casefold()
    for key, value in mapping.items():
        if str(key).casefold() == target and isinstance(value, dict):
            return value
    return {}


def _write_source_list(values: list[str]) -> str:
    body = "\n".join(_unique_sources(values))
    return SOURCE_HEADER + ("\n\n" + body if body else "") + "\n"


def _filtered_source_request_markup(
    markup: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(markup, dict):
        return markup
    result = copy.deepcopy(markup)
    rows: list[list[dict[str, Any]]] = []
    for row in result.get("inline_keyboard", []):
        if not isinstance(row, list):
            continue
        new_row: list[dict[str, Any]] = []
        for raw in row:
            if not isinstance(raw, dict):
                continue
            button = dict(raw)
            callback = str(button.get("callback_data") or "")
            if callback.startswith("sr:nightly:"):
                continue
            if callback.startswith("sr:fast:"):
                button["text"] = "➕ Добавить в источники"
            new_row.append(button)
        if new_row:
            rows.append(new_row)
    result["inline_keyboard"] = rows
    return result


def install(panel_class: type[Any]) -> None:
    """Collapse the live Telegram panel to one source base and remove obsolete pages."""

    if getattr(panel_class, "_bbvg_single_source_model_installed", False):
        return

    original_setup_bot: Callable = panel_class.setup_bot
    original_handle_message: Callable = panel_class.handle_message
    original_handle_callback: Callable = panel_class.handle_callback
    original_render_page: Callable = panel_class.render_page
    original_notify_moderators: Callable = panel_class.notify_moderators
    original_decide_source_request: Callable = panel_class.decide_source_request
    original_notification_options: Callable = panel_class._notification_options_for_role

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        return [
            [
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
                {"text": "📡 Источники", "callback_data": "page:sources"},
            ],
            [
                {"text": "⚙️ Настройки", "callback_data": "page:settings"},
                {
                    "text": "🛠 Управление" if admin else "✅ Работа системы",
                    "callback_data": "page:control" if admin else "page:status",
                },
            ],
        ]

    @staticmethod
    def source_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = [
            [{"text": "🏆 Рейтинг источников", "callback_data": "page:ranking"}],
        ]
        if admin:
            rows.append(
                [
                    {"text": "📋 Все источники", "callback_data": "source_list:primary:0"},
                    {"text": "➕ Добавить источник", "callback_data": "source:add"},
                ]
            )
        else:
            rows.append(
                [
                    {"text": "📋 Источники", "callback_data": "source_list:primary:0"},
                    {"text": "➕ Предложить источник", "callback_data": "source:request"},
                ]
            )
        return rows

    @staticmethod
    def source_mode_name(_mode: str) -> str:
        return "Источники"

    @staticmethod
    def notification_options_for_role(role: str) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            item
            for item in original_notification_options(role)
            if str(item[0]) not in SUMMARY_NOTIFICATION_KEYS
        )

    def setup_bot(self: Any) -> None:
        original_setup_bot(self)
        commands = [
            dict(item)
            for item in getattr(__import__("admin_bot"), "COMMANDS", [])
            if str(item.get("command") or "") not in {"stats", "reports"}
        ]
        for item in commands:
            if item.get("command") == "ranking":
                item["description"] = "Рейтинг источников"
        self.telegram_api("setMyCommands", {"commands": commands})

    def source_sets(self: Any, snap: Any) -> dict[str, list[str]]:
        sources = _unique_sources(list(getattr(snap, "fast", []) or []))
        return {"primary": sources}

    def show_sources(self: Any) -> None:
        snap = self.snapshot(force=True)
        sources = _unique_sources(list(getattr(snap, "fast", []) or []))
        health = snap.health.get("sources", {}) if isinstance(snap.health, dict) else {}
        problems = 0
        if isinstance(health, dict):
            for source in sources:
                entry = _source_entry(health, source)
                if entry and str(entry.get("status") or "").casefold() not in {"", "ok"}:
                    problems += 1
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"В общей базе: <b>{len(sources)}</b>",
            "Все источники проверяются одним монитором с интервалом из настроек.",
        ]
        if problems:
            lines.append(f"Требуют внимания по доступности: <b>{problems}</b>")
        rows = self.source_menu_rows(self.is_admin())
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_source_list(self: Any, _group: str, page: int = 0) -> None:
        snap = self.snapshot()
        rows = _unique_sources(list(getattr(snap, "fast", []) or []))
        per_page = 10
        max_page = max(0, (len(rows) - 1) // per_page)
        page = max(0, min(int(page), max_page))
        part = rows[page * per_page : (page + 1) * per_page]
        lines = [f"📡 <b>Источники: {len(rows)}</b>", ""]
        buttons: list[list[dict[str, str]]] = []
        stats_sources = snap.stats.get("sources", {}) if isinstance(snap.stats, dict) else {}
        for source in part:
            stats = _source_entry(stats_sources, source)
            wheels = int(stats.get("wheel_posts", 0) or 0)
            lines.append(f"• @{html.escape(source)} — колёс: {wheels}")
            buttons.append(
                [{"text": f"@{source}", "callback_data": f"source_detail:{source}"}]
            )
        if not part:
            lines.append("Список пуст.")
        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append(
                {"text": "◀️", "callback_data": f"source_list:primary:{page - 1}"}
            )
        if page < max_page:
            pager.append(
                {"text": "▶️", "callback_data": f"source_list:primary:{page + 1}"}
            )
        if pager:
            buttons.append(pager)
        self.send("\n".join(lines), reply_markup=self.with_nav(buttons))

    def show_source_detail(self: Any, source: str) -> None:
        source = self.safe_source(source)
        snap = self.snapshot()
        configured = {value.casefold() for value in getattr(snap, "fast", []) or []}
        stats_sources = snap.stats.get("sources", {}) if isinstance(snap.stats, dict) else {}
        health_sources = snap.health.get("sources", {}) if isinstance(snap.health, dict) else {}
        stats = _source_entry(stats_sources, source)
        health = _source_entry(health_sources, source)
        raw_status = str(health.get("status") or "unknown")
        text = (
            f"📡 <b>@{html.escape(source)}</b>\n\n"
            f"В общей базе: <b>{'да' if source.casefold() in configured else 'нет'}</b>\n"
            f"Состояние: {html.escape(self.source_status_name(raw_status))}\n"
            f"Проверок: {int(stats.get('checks', 0) or 0)}\n"
            f"Постов с колёсами: {int(stats.get('wheel_posts', 0) or 0)}\n"
            f"Последнее колесо: {self.fmt_dt(stats.get('last_wheel_post_at'))}\n"
            f"Последняя проверка: {self.fmt_dt(health.get('last_checked_at'))}"
        )
        buttons: list[list[dict[str, str]]] = [
            [{"text": "Открыть Telegram", "url": f"https://telegram.me/{source}"}]
        ]
        if self.is_admin() and source.casefold() in configured:
            if raw_status == "quarantined":
                buttons.append(
                    [{"text": "▶️ Возобновить проверки", "callback_data": f"source:clearq:{source}"}]
                )
            buttons.append(
                [{"text": "🗑 Удалить", "callback_data": f"source:removeask:{source}"}]
            )
        self.send(text, reply_markup=self.with_nav(buttons))

    def set_source_mode(self: Any, source: str, mode: str) -> str:
        if not self.is_admin():
            raise PermissionError("Недостаточно прав")
        source = self.safe_source(source)
        normalized = str(mode or "").casefold()
        if normalized not in {"fast", "primary", "add", "nightly", "reserve", "remove"}:
            raise ValueError("Неизвестное действие с источником")
        text, _ = self.get_file("public_sources.txt")
        values = self.parse_list(text)
        target = source.casefold()
        values = [value for value in values if value.casefold() != target]
        if normalized != "remove":
            available, detail = self.verify_public_source(source)
            if not available:
                raise ValueError(detail)
            values.append(source)
        new_text = _write_source_list(values)
        if new_text != text:
            action = "Add" if normalized != "remove" else "Remove"
            self.update_file(
                "public_sources.txt",
                new_text,
                f"{action} @{source} in unified source base via Telegram [skip ci]",
            )
        self.cache = None
        refresh = getattr(self, "refresh_source_runtime", None)
        if callable(refresh):
            refresh()
        return (
            f"@{source} добавлен в общую базу источников."
            if normalized != "remove"
            else f"@{source} удалён из общей базы источников."
        )

    def notify_moderators(self: Any, request_id: str, request: dict[str, Any]) -> None:
        original_send = self.send

        def filtered_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            return original_send(
                text,
                reply_markup=_filtered_source_request_markup(reply_markup),
                chat_id=chat_id,
            )

        self.send = filtered_send
        try:
            original_notify_moderators(self, request_id, request)
        finally:
            self.send = original_send

    def decide_source_request(
        self: Any,
        action: str,
        request_id: str,
    ) -> tuple[str, dict[str, Any]]:
        normalized = "fast" if action in {"add", "fast", "nightly", "reserve"} else action
        return original_decide_source_request(self, normalized, request_id)

    def show_removed_page(self: Any, *_args: Any, **_kwargs: Any) -> None:
        self.show_menu(clear_stack=True)

    def render_page(self: Any, page: str) -> None:
        value = str(page or "")
        if value.startswith(REMOVED_PAGE_PREFIXES):
            self.show_menu(clear_stack=True)
            return
        if value.startswith("source_list:reserve:") or value.startswith("source_list:quiet:"):
            _, _, page_no = value.split(":", 2)
            self.show_source_list("primary", int(page_no))
            return
        original_render_page(self, value)

    def handle_message(self: Any, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        command = text.split("@", 1)[0].split(maxsplit=1)[0].casefold() if text else ""
        if command in {"/stats", "/reports"} or text in {
            "📊 Статистика",
            "📊 Аналитика",
            "📅 Отчёты",
            "📭 Давно без колёс",
            "🌙 Ночное наблюдение",
            "🛰️ Разведка источников",
            "🔎 Поиск новых источников",
        }:
            self.show_menu(clear_stack=True)
            return
        original_handle_message(self, message)

    def handle_callback(self: Any, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")
        if data.startswith(REMOVED_CALLBACK_PREFIXES):
            try:
                self._prepare_callback_user(query)
                self.answer(query_id, "Раздел удалён")
                self.show_menu(clear_stack=True)
            except Exception:
                pass
            return
        if data.startswith("source_list:reserve:") or data.startswith("source_list:quiet:"):
            data = "source_list:primary:" + data.rsplit(":", 1)[1]
            query = dict(query)
            query["data"] = data
        elif data.startswith("source:move:nightly:"):
            query = dict(query)
            query["data"] = "source:move:fast:" + data.split(":", 3)[3]
        elif data.startswith("intel:mode:nightly:"):
            query = dict(query)
            query["data"] = "intel:mode:fast:" + data.split(":", 3)[3]
        original_handle_callback(self, query)

    panel_class.compact_menu_rows = compact_menu_rows
    panel_class.source_menu_rows = source_menu_rows
    panel_class.source_mode_name = source_mode_name
    panel_class._notification_options_for_role = notification_options_for_role
    panel_class.setup_bot = setup_bot
    panel_class.source_sets = source_sets
    panel_class.show_sources = show_sources
    panel_class.show_source_list = show_source_list
    panel_class.show_source_detail = show_source_detail
    panel_class.set_source_mode = set_source_mode
    panel_class.notify_moderators = notify_moderators
    panel_class.decide_source_request = decide_source_request
    panel_class.show_analytics = show_removed_page
    panel_class.show_stats = show_removed_page
    panel_class.show_reports = show_removed_page
    panel_class.show_period_report = show_removed_page
    panel_class.show_inactive_report = show_removed_page
    panel_class.show_intelligence = show_removed_page
    panel_class.show_intelligence_list = show_removed_page
    panel_class.show_intelligence_detail = show_removed_page
    panel_class.show_discovery = show_removed_page
    panel_class.render_page = render_page
    panel_class.handle_message = handle_message
    panel_class.handle_callback = handle_callback
    panel_class._bbvg_single_source_model_installed = True


def self_test() -> None:
    assert _unique_sources(["@One", "one", "Two"]) == ["One", "Two"]
    assert "Ночное" not in SOURCE_HEADER
    assert "Давно без колёс" not in SOURCE_HEADER
    assert _write_source_list(["One"]).endswith("\n\nOne\n")
    markup = {
        "inline_keyboard": [[
            {"text": "fast", "callback_data": "sr:fast:abc"},
            {"text": "night", "callback_data": "sr:nightly:abc"},
        ]]
    }
    cleaned = _filtered_source_request_markup(markup)
    assert cleaned is not None
    buttons = cleaned["inline_keyboard"][0]
    assert [button.get("callback_data") for button in buttons] == ["sr:fast:abc"]
    assert buttons[0]["text"] == "➕ Добавить в источники"
    print("single source model simplification self-test passed")


if __name__ == "__main__":
    self_test()
