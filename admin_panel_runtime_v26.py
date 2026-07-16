from __future__ import annotations

import argparse
import base64
import html
import inspect
import json
from typing import Any
from urllib.parse import quote

import admin_action_v2
import admin_bot as legacy
import bot_private_state
from admin_panel_runtime_v17 import default_source_requests
from admin_panel_runtime_v25 import TelegramPanelRuntimeV25
from bbvg.bot.sources import SourceRegistryRuntime


class TelegramPanelRuntimeV26(TelegramPanelRuntimeV25):
    """Bot-only v26: reliable synchronous admin wheel actions and compact menu."""

    @staticmethod
    def compact_menu_rows(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [
            [
                {"text": "📊 Статистика", "callback_data": "page:stats:1"},
                {"text": "🔥 Активные колёса", "callback_data": "page:active"},
            ],
            [{"text": "📡 Источники", "callback_data": "page:sources"}],
        ]
        if admin:
            rows[1].append({"text": "⚙️ Настройки", "callback_data": "page:settings"})
        return rows

    def _read_json_at(
        self,
        path: str,
        commit_sha: str,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.gh_request(
            "GET",
            f"/repos/{legacy.GITHUB_REPOSITORY}/contents/{quote(path, safe='/')}"
            f"?ref={quote(commit_sha, safe='')}",
        )
        payload = response.json()
        try:
            text = base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
            value = json.loads(text)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return dict(default)
        return value if isinstance(value, dict) else dict(default)

    @staticmethod
    def _serialize_json(value: dict[str, Any]) -> str:
        """Serialize a state file without shadowing the inherited JSON parser."""
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _apply_admin_action_direct(self, action: str, value: str) -> dict[str, Any]:
        """Apply an administrator action in one atomic Git commit.

        The previous implementation only dispatched a second workflow. Telegram
        acknowledged the button before that workflow had changed anything, and a
        failed/cancelled workflow left the button looking non-functional. Here the
        active bot writes the state itself and only returns after the ref update.
        """

        ref_path = (
            f"/repos/{legacy.GITHUB_REPOSITORY}/git/ref/heads/"
            f"{quote(legacy.GITHUB_BRANCH, safe='')}"
        )
        for attempt in range(1, 4):
            ref = self.gh_request("GET", ref_path).json()
            parent_sha = str((ref.get("object") or {}).get("sha") or "")
            if not parent_sha:
                raise RuntimeError("Не удалось определить текущий commit main")
            commit = self.gh_request(
                "GET",
                f"/repos/{legacy.GITHUB_REPOSITORY}/git/commits/{parent_sha}",
            ).json()
            base_tree = str((commit.get("tree") or {}).get("sha") or "")
            if not base_tree:
                raise RuntimeError("Не удалось определить дерево main")

            state = self._read_json_at("state.json", parent_sha, {})
            health = self._read_json_at(
                "source_health.json", parent_sha, {"version": 1, "sources": {}}
            )
            stats = self._read_json_at(
                "source_stats.json", parent_sha, {"version": 1, "sources": {}, "daily": {}}
            )
            result = admin_action_v2.legacy.apply_action(
                state,
                health,
                stats,
                action,
                value,
            )

            changed: list[tuple[str, dict[str, Any]]] = []
            if result.get("state_changed"):
                changed.append(("state.json", state))
            if result.get("health_changed"):
                changed.append(("source_health.json", health))
            if result.get("stats_changed"):
                changed.append(("source_stats.json", stats))
            if not changed:
                return result

            tree_entries: list[dict[str, str]] = []
            for path, payload in changed:
                blob = self.gh_request(
                    "POST",
                    f"/repos/{legacy.GITHUB_REPOSITORY}/git/blobs",
                    json_body={"content": self._serialize_json(payload), "encoding": "utf-8"},
                    expected=(201,),
                ).json()
                tree_entries.append(
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": str(blob.get("sha") or ""),
                    }
                )

            tree = self.gh_request(
                "POST",
                f"/repos/{legacy.GITHUB_REPOSITORY}/git/trees",
                json_body={"base_tree": base_tree, "tree": tree_entries},
                expected=(201,),
            ).json()
            new_commit = self.gh_request(
                "POST",
                f"/repos/{legacy.GITHUB_REPOSITORY}/git/commits",
                json_body={
                    "message": f"Apply BB V.G. administrator action: {action} [skip ci]",
                    "tree": str(tree.get("sha") or ""),
                    "parents": [parent_sha],
                },
                expected=(201,),
            ).json()
            try:
                self.gh_request(
                    "PATCH",
                    ref_path,
                    json_body={"sha": str(new_commit.get("sha") or ""), "force": False},
                    expected=(200,),
                )
            except RuntimeError as exc:
                if " 422 " in f" {exc} " and attempt < 3:
                    continue
                raise

            with self.snapshot_lock:
                self.snapshot_value = None
                self.snapshot_updated_at = 0.0
            self.refresh_requested.set()
            return result

        raise RuntimeError("Не удалось сохранить действие после трёх попыток")

    def dispatch_admin_action(self, action: str, value: str) -> dict[str, Any]:
        result = self._apply_admin_action_direct(action, value)
        try:
            self.dispatch(
                "monitor.yml",
                {"continuous": "true", "replace": "true"},
            )
        except Exception as exc:
            print(
                "WARNING monitor replacement after administrator action: "
                f"{type(exc).__name__}: {exc}"
            )
        return result

    def _prepare_callback_user(self, query: dict[str, Any]) -> None:
        message = query.get("message") if isinstance(query, dict) else None
        message = message if isinstance(message, dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = query.get("from") if isinstance(query, dict) else None
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(sender.get("id") or "")
        self.set_context(chat.get("id"), sender.get("id"))
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        if self.private_chat({"chat": chat, "from": sender}) and user_id and user_id not in users:
            self.register_user({"chat": chat, "from": sender})
        self.set_context(chat.get("id"), sender.get("id"))

    def handle_callback(self, query: dict[str, Any]) -> None:
        self._prepare_callback_user(query)
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")

        admin_action: tuple[str, str, str] | None = None
        if self.is_admin() and data.startswith("wheel:part:"):
            key = data.split(":", 2)[2].casefold()
            admin_action = ("participate_wheel", key, "Участие подтверждено")
        elif self.is_admin() and data.startswith("wheel:inactive:"):
            key = data.split(":", 2)[2].casefold()
            admin_action = (
                "mark_inactive_global",
                f"{key}|{self.current_user_id or 'admin'}",
                "Колесо удалено для всех пользователей",
            )
        elif self.is_admin() and data.startswith("bb:p:"):
            token = data.split(":", 2)[2]
            admin_action = ("participate_token", token, "Участие подтверждено")
        elif self.is_admin() and data.startswith("bb:x:"):
            key = data.split(":", 2)[2].casefold()
            admin_action = (
                "mark_inactive_global",
                f"{key}|{self.current_user_id or 'admin'}",
                "Колесо удалено для всех пользователей",
            )

        if admin_action is None:
            # Context/registration was already prepared above. Continue with the
            # consolidated source/user callback chain while skipping the historic
            # v25 registration wrapper.
            SourceRegistryRuntime.handle_callback(self, query)
            return

        action, value, success_text = admin_action
        self.answer(query_id, "Сохраняю действие")
        try:
            result = self.dispatch_admin_action(action, value)
            self.refresh_snapshot()
        except Exception as exc:
            print(f"ERROR direct administrator action {action}: {type(exc).__name__}: {exc}")
            self.send(
                "⚠️ <b>Действие не сохранено.</b>\n\n"
                f"Ошибка: <code>{html.escape(type(exc).__name__)}</code>."
            )
            return

        detail = str(result.get("detail") or success_text)
        if result.get("queued"):
            self.send(f"⏳ <b>Команда принята</b>\n{html.escape(detail)}")
            return
        self.send(f"✅ <b>{html.escape(success_text)}</b>\n{html.escape(detail)}")
        self.show_active()


def self_test() -> None:
    bot_private_state.self_test()
    callbacks = {
        button.get("callback_data")
        for row in TelegramPanelRuntimeV26.compact_menu_rows(True)
        for button in row
    }
    assert callbacks == {
        "page:stats:1",
        "page:active",
        "page:sources",
        "page:settings",
    }
    assert "page:discovery" not in callbacks
    assert "page:intelligence" not in callbacks
    source_page = inspect.getsource(TelegramPanelRuntimeV25.show_sources)
    assert "page:discovery" in source_page
    assert "page:intelligence" in source_page

    panel = TelegramPanelRuntimeV26()
    assert panel._json_text("{}", {}) == {}, "Inherited JSON parser must keep its two-argument contract"
    assert '"ok": true' in panel._serialize_json({"ok": True})
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
    access["access_signature"] = panel._signature(access)
    panel._bot_bundle = bot_private_state.default_bundle(access, default_source_requests())
    panel.load_access()
    saves: list[str] = []
    actions: list[tuple[str, str]] = []
    panel._save_bot_bundle = lambda message: saves.append(message) or True  # type: ignore[method-assign]
    panel.dispatch_admin_action = (  # type: ignore[method-assign]
        lambda action, value: actions.append((action, value)) or {"detail": "ok"}
    )
    panel.answer = lambda *args, **kwargs: None  # type: ignore[method-assign]
    panel.send = lambda *args, **kwargs: {"ok": True}  # type: ignore[method-assign]
    panel.refresh_snapshot = lambda: None  # type: ignore[method-assign]
    panel.show_active = lambda: None  # type: ignore[method-assign]

    base_query = {
        "id": "callback-test",
        "from": {"id": 1, "username": "owner", "first_name": "Owner"},
        "message": {"message_id": 1, "chat": {"id": 1, "type": "private"}},
    }
    panel.handle_callback({**base_query, "data": "wheel:part:test-wheel"})
    panel.handle_callback({**base_query, "data": "wheel:inactive:test-wheel"})
    assert actions == [
        ("participate_wheel", "test-wheel"),
        ("mark_inactive_global", "test-wheel|1"),
    ]
    assert saves == [], "Known administrator callbacks must not rewrite user state first"
    print("admin_panel_runtime_v26 administrator button self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV26().run()


if __name__ == "__main__":
    raise SystemExit(main())
