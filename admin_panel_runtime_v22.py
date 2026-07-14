from __future__ import annotations

import argparse
import html
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import requests

import admin_bot as legacy
from admin_panel_runtime_v20 import BRAND_NAME
from admin_panel_runtime_v21 import TelegramPanelRuntimeV21


UTC = timezone.utc
POLLING_STATE_PATH = "telegram_polling_state.json"
POLLING_STATE_VERSION = 1


class TelegramPanelRuntimeV22(TelegramPanelRuntimeV21):
    """Current panel with durable update offsets and safe button handling."""

    def __init__(self) -> None:
        super().__init__()
        self._persisted_offset: int | None = None

    def _refresh_callback_context(self, query: dict[str, Any]) -> None:
        # Roles can be changed by a different long-running process. Always read
        # the latest access file before authorizing a button press.
        self.load_access(force=True)
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = query.get("from") if isinstance(query, dict) else None
        self.set_context(
            chat.get("id") if isinstance(chat, dict) else None,
            sender.get("id") if isinstance(sender, dict) else None,
        )

    def _participation_key_from_token(self, token: str) -> str:
        context = self.snapshot().state.get("button_contexts", {}).get(token)
        if not isinstance(context, dict):
            raise ValueError("Контекст кнопки устарел")
        key = str(context.get("wheel_key") or context.get("identifier") or "").casefold()
        if not key:
            raise ValueError("Колесо не определено")
        return key

    def handle_callback(self, query: dict[str, Any]) -> None:
        self._refresh_callback_context(query)
        query_id = str(query.get("id") or "")
        data = str(query.get("data") or "")

        if self.current_role == "blocked" or not self.can_view():
            self.answer(query_id, "Недоступно")
            return

        try:
            if data.startswith("bb:p:"):
                token = data.split(":", 2)[2]
                if self.is_admin():
                    self.dispatch_admin_action("participate_token", token)
                    self.answer(query_id, "Действие принято")
                    self.send(
                        "✅ <b>Действие принято.</b>\n\n"
                        "Колесо подтверждается для всех пользователей.",
                        reply_markup=self.with_nav(),
                    )
                else:
                    key = self._participation_key_from_token(token)
                    self.mark_personal_participation(key)
                    self.answer(query_id, "Ваше участие отмечено")
                    self.send(
                        "✅ <b>Ваше участие отмечено.</b>\n\n"
                        f"Колесо: <code>{html.escape(key)}</code>.",
                        reply_markup=self.with_nav(),
                    )
                return

            if data.startswith("wheel:part:"):
                key = data.split(":", 2)[2].casefold()
                if self.is_admin():
                    self.dispatch_admin_action("participate_wheel", key)
                    self.answer(query_id, "Действие принято")
                    self.send(
                        "✅ <b>Действие принято.</b>\n\n"
                        f"Колесо <code>{html.escape(key)}</code> подтверждается для всех пользователей.",
                        reply_markup=self.with_nav(),
                    )
                else:
                    self.mark_personal_participation(key)
                    self.answer(query_id, "Ваше участие отмечено")
                    self.send(
                        "✅ <b>Ваше участие отмечено.</b>\n\n"
                        f"Колесо: <code>{html.escape(key)}</code>.",
                        reply_markup=self.with_nav(),
                    )
                return
        except PermissionError:
            self.answer(query_id, "Недоступно для вашей роли")
            return
        except Exception as exc:
            print(f"ERROR participation callback {data}: {type(exc).__name__}: {exc}")
            self.answer(query_id, "Не удалось выполнить действие")
            self.send(
                "⚠️ Не удалось сохранить действие. Попробуйте ещё раз.",
                reply_markup=self.with_nav(),
            )
            return

        super().handle_callback(query)

    def show_active(self) -> None:
        items = self._collect_current_wheels()
        snap = self.snapshot()
        participating = self._joined_wheel_keys(snap)
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

            lines.append(f"<b>{index}. <code>{html.escape(identifier)}</code></b>")
            if deadline:
                # When time is known, only the live countdown is useful.
                lines.append(f"⏳ {html.escape(self.remaining(deadline))}")
            else:
                lines.append("🔴 Время прокрутки неизвестно")
            lines.extend(
                [
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

    def _load_polling_offset(self) -> int:
        value = self.get_json_file(
            POLLING_STATE_PATH,
            {"version": POLLING_STATE_VERSION, "next_update_id": 0},
        )
        try:
            return max(0, int(value.get("next_update_id", 0)))
        except (TypeError, ValueError):
            return 0

    def _persist_polling_offset(self, *, force: bool = False) -> None:
        offset = max(0, int(self.offset or 0))
        if not force and self._persisted_offset == offset:
            return
        payload = {
            "version": POLLING_STATE_VERSION,
            "next_update_id": offset,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            self.update_file(
                POLLING_STATE_PATH,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                "Persist Telegram control-center update offset [skip ci]",
            )
        except Exception as exc:
            print(f"WARNING polling offset persistence: {type(exc).__name__}: {exc}")
            return
        self._persisted_offset = offset

    def _initialize_polling_offset(self) -> None:
        stored = self._load_polling_offset()
        if stored > 0:
            self.offset = stored
            self._persisted_offset = stored
            return

        # The old runtime kept the offset only in memory. On the first v22 run,
        # acknowledge the already queued history without executing old buttons.
        response = self.telegram_api(
            "getUpdates",
            {
                "timeout": 0,
                "allowed_updates": ["message", "callback_query", "my_chat_member"],
            },
        )
        update_ids = [
            int(item.get("update_id", 0))
            for item in response.get("result", [])
            if isinstance(item, dict)
        ]
        self.offset = max(update_ids) + 1 if update_ids else 0
        self._persist_polling_offset(force=True)

    def run(self) -> int:
        if (
            not legacy.BOT_TOKEN
            or not legacy.BOT_CHAT_ID
            or not legacy.GITHUB_TOKEN
            or not legacy.GITHUB_REPOSITORY
        ):
            raise RuntimeError(
                "BOT_TOKEN, BOT_CHAT_ID, GITHUB_TOKEN and GITHUB_REPOSITORY are required"
            )

        self.load_access(force=True)
        self.setup_bot()
        self._initialize_polling_offset()
        refresh_thread = threading.Thread(
            target=self.refresh_loop,
            name="snapshot-refresh",
            daemon=True,
        )
        refresh_thread.start()
        print(
            f"Telegram panel v22 started for {legacy.GITHUB_REPOSITORY}; "
            f"run_seconds={legacy.RUN_SECONDS}; offset={self.offset or 0}"
        )
        deadline = time.monotonic() + legacy.RUN_SECONDS
        try:
            while time.monotonic() < deadline:
                try:
                    payload: dict[str, Any] = {
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query", "my_chat_member"],
                        "offset": int(self.offset or 0),
                    }
                    response = self.telegram_api("getUpdates", payload)
                    updates = response.get("result", [])
                    for update in updates:
                        if not isinstance(update, dict):
                            continue
                        update_id = int(update.get("update_id", 0))
                        self.offset = max(int(self.offset or 0), update_id + 1)
                        try:
                            self.handle_update(update)
                        except Exception as exc:
                            print(f"ERROR update {update_id}: {type(exc).__name__}: {exc}")
                    if updates:
                        self._persist_polling_offset()
                except requests.RequestException as exc:
                    print(f"WARNING polling network error: {type(exc).__name__}: {exc}")
                    time.sleep(3)
                except Exception as exc:
                    print(f"WARNING polling error: {type(exc).__name__}: {exc}")
                    time.sleep(2)
        finally:
            self._persist_polling_offset(force=True)
            self.stop_refresh.set()
            self.refresh_requested.set()
        print("Telegram panel v22 shift completed normally.")
        return 0


def self_test() -> None:
    access = {
        "version": 2,
        "owner_id": "1",
        "admins": [],
        "users": {
            "1": {"id": "1", "chat_id": "1"},
            "2": {"id": "2", "chat_id": "2"},
        },
        "blocked_users": [],
        "notification_recipients": [],
        "settings": {"public_panel": True, "notifications": True},
    }
    bot = TelegramPanelRuntimeV22()
    force_reads: list[bool] = []
    answers: list[str] = []
    sent: list[str] = []
    personal: list[str] = []
    admin_actions: list[tuple[str, str]] = []
    bot.load_access = lambda force=False: force_reads.append(force) or access  # type: ignore[method-assign]
    bot.can_view = lambda: True  # type: ignore[method-assign]
    bot.snapshot = lambda: SimpleNamespace(  # type: ignore[method-assign]
        state={"button_contexts": {"token": {"wheel_key": "wheel-one"}}}
    )
    bot.mark_personal_participation = lambda key: personal.append(key)  # type: ignore[method-assign]
    bot.dispatch_admin_action = lambda action, value: admin_actions.append((action, value))  # type: ignore[method-assign]
    bot.answer = lambda query_id, text="Готово": answers.append(text)  # type: ignore[method-assign]
    bot.send = lambda text, **kwargs: sent.append(text) or {}  # type: ignore[method-assign]

    bot.handle_callback(
        {
            "id": "user-query",
            "from": {"id": 2},
            "message": {"chat": {"id": 2, "type": "private"}},
            "data": "bb:p:token",
        }
    )
    assert personal == ["wheel-one"]
    assert answers[-1] == "Ваше участие отмечено"
    assert "Ваше участие отмечено" in sent[-1]

    bot.handle_callback(
        {
            "id": "admin-query",
            "from": {"id": 1},
            "message": {"chat": {"id": 1, "type": "private"}},
            "data": "wheel:part:wheel-two",
        }
    )
    assert admin_actions == [("participate_wheel", "wheel-two")]
    assert answers[-1] == "Действие принято"
    assert "подтверждается для всех" in sent[-1]
    assert True in force_reads

    display = TelegramPanelRuntimeV22()
    rendered: list[str] = []
    now = datetime.now(UTC)
    display._collect_current_wheels = lambda: [  # type: ignore[method-assign]
        {
            "_key": "known",
            "identifier": "known",
            "source": "source_one",
            "url": "https://example.com/known",
            "deadline": (now + timedelta(minutes=30)).isoformat(),
        },
        {
            "_key": "unknown",
            "identifier": "unknown",
            "source": "source_two",
            "url": "https://example.com/unknown",
        },
    ]
    display.snapshot = lambda: SimpleNamespace(state={"participating_wheels": {}})  # type: ignore[method-assign]
    display._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
    display.send = lambda text, **kwargs: rendered.append(text) or {}  # type: ignore[method-assign]
    display.current_role = "user"
    display.show_active()
    assert rendered
    assert "🟡 время прокрутки известно" not in rendered[-1]
    assert "🔴 Время прокрутки неизвестно" in rendered[-1]
    print("admin_panel_runtime_v22 callback and polling self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV22().run()


if __name__ == "__main__":
    raise SystemExit(main())
