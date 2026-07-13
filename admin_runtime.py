from __future__ import annotations

import argparse
from typing import Any

import admin_bot


BLOCKED_SOURCES = {"frixa_betboom", "gazazor"}


class RuntimeAdminBot(admin_bot.AdminBot):
    def verify_public_source(self, username: str) -> tuple[bool, str]:
        if username.casefold() in BLOCKED_SOURCES:
            return False, "источник ранее исключён и заблокирован для повторного добавления"
        return super().verify_public_source(username)

    def set_source_mode(self, username: str, mode: str) -> str:
        username = self.safe_source(username)
        if mode != "remove" and username.casefold() in BLOCKED_SOURCES:
            raise ValueError(
                f"@{username} ранее исключён из мониторинга и заблокирован для повторного добавления"
            )
        result = super().set_source_mode(username, mode)
        # Commits made with GITHUB_TOKEN do not start push workflows, therefore
        # restart the continuous monitor explicitly after every source-list change.
        self.dispatch("monitor.yml", {"continuous": "true"})
        return result

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data != "source:add":
            super().handle_callback(query)
            return
        query_id = str(query.get("id") or "")
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = query.get("from") if isinstance(query, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        user_id = sender.get("id") if isinstance(sender, dict) else None
        if not self.authorized(chat_id, user_id):
            self.answer(query_id, "Недоступно")
            return
        self.pending_input[int(user_id)] = {"kind": "add_source"}
        self.answer(query_id, "Жду username")
        self.send(
            "➕ Отправьте публичный username Telegram-канала или чата без ссылки.\n\n"
            "Пример: <code>channel_name</code>"
        )


def self_test() -> None:
    admin_bot.self_test()
    bot = RuntimeAdminBot()
    available, _ = bot.verify_public_source("gazazor")
    assert not available
    assert "gazazor" in BLOCKED_SOURCES
    assert "frixa_betboom" in BLOCKED_SOURCES
    print("admin_runtime self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return RuntimeAdminBot().run()


if __name__ == "__main__":
    raise SystemExit(main())
