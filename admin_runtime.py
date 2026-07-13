from __future__ import annotations

import argparse

import admin_bot


BLOCKED_SOURCES = {"frixa_betboom", "gazazor"}


class RuntimeAdminBot(admin_bot.AdminBot):
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


def self_test() -> None:
    admin_bot.self_test()
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
