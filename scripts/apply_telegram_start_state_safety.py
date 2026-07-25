from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {text.count(old)}")
    return text.replace(old, new, 1)


panel_path = ROOT / "admin_panel_v2.py"
panel = panel_path.read_text(encoding="utf-8")

class_marker = "\n\nclass TelegramPanelV2(RuntimeAdminBot):\n"
class_insert = '''\n\nclass SnapshotUnavailableError(RuntimeError):
    """Critical monitor files could not be read without inventing empty state."""


class TelegramPanelV2(RuntimeAdminBot):
'''
panel = replace_once(panel, class_marker, class_insert, label="snapshot exception class")

refresh_start = panel.index("    def refresh_snapshot(self) -> Snapshot:\n")
refresh_end = panel.index("\n    def snapshot(self, *, force: bool = False) -> Snapshot:\n", refresh_start)
new_refresh = '''    def refresh_snapshot(self) -> Snapshot:
        files = {
            "state": "state.json",
            "stats": "source_stats.json",
            "health": "source_health.json",
            "discovery": "discovery_state.json",
            "unknown": "unknown_timer_samples.json",
            "fast": "public_sources.txt",
            "nightly": "source_catalog.txt",
        }
        values: dict[str, str] = {}
        failures: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(files)) as pool:
            futures = {pool.submit(self._direct_get_file, path): key for key, path in files.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    values[key] = future.result()
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    failures[key] = detail
                    print(f"WARNING snapshot {key}: {detail}")

        parsed: dict[str, dict[str, Any]] = {}
        json_defaults: dict[str, dict[str, Any]] = {
            "state": {},
            "stats": {"sources": {}, "daily": {}},
            "health": {"sources": {}},
            "discovery": {},
            "unknown": {"samples": []},
        }
        for key in json_defaults:
            raw = values.get(key)
            if raw is None:
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("top-level JSON value is not an object")
                parsed[key] = value
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                failures[key] = f"{type(exc).__name__}: {exc}"

        if "fast" in values and not values["fast"].strip():
            failures["fast"] = "configured source list is empty"

        critical = {"state", "stats", "health", "fast"}
        critical_failures = sorted(critical.intersection(failures))
        with self.snapshot_lock:
            current = self.snapshot_value
        if failures and current is not None:
            print(
                "WARNING snapshot refresh kept the last verified snapshot; failures="
                + ",".join(sorted(failures))
            )
            return current
        if critical_failures:
            raise SnapshotUnavailableError(
                "Не удалось загрузить обязательные данные мониторинга: "
                + ", ".join(critical_failures)
            )

        snap = Snapshot(
            state=parsed.get("state", json_defaults["state"]),
            stats=parsed.get("stats", json_defaults["stats"]),
            health=parsed.get("health", json_defaults["health"]),
            discovery=parsed.get("discovery", json_defaults["discovery"]),
            unknown=parsed.get("unknown", json_defaults["unknown"]),
            fast=self.parse_list(values.get("fast", "")),
            nightly=self.parse_list(values.get("nightly", "")),
        )
        with self.snapshot_lock:
            self.snapshot_value = snap
            self.snapshot_updated_at = time.monotonic()
        return snap
'''
panel = panel[:refresh_start] + new_refresh + panel[refresh_end:]

handler_marker = '''    # ---------- Handlers ----------
    def handle_message(self, message: dict[str, Any]) -> None:
'''
handler_replacement = '''    # ---------- Handlers ----------
    def handle_message(self, message: dict[str, Any]) -> None:
        try:
            self._handle_message_impl(message)
        except Exception as exc:
            print(f"ERROR Telegram message: {type(exc).__name__}: {exc}")
            chat = message.get("chat") if isinstance(message, dict) else None
            sender = message.get("from") if isinstance(message, dict) else None
            chat = chat if isinstance(chat, dict) else {}
            sender = sender if isinstance(sender, dict) else {}
            self.current_chat_id = str(chat.get("id")) if chat.get("id") is not None else None
            self.current_user_id = str(sender.get("id")) if sender.get("id") is not None else None
            try:
                self.send(
                    "⚠️ <b>Панель временно не смогла загрузить данные.</b>\n\n"
                    "Сохранённые данные не обнулены. Повторите команду /start через несколько секунд."
                )
            except Exception as send_exc:
                print(
                    "ERROR Telegram fallback response: "
                    f"{type(send_exc).__name__}: {send_exc}"
                )

    def _handle_message_impl(self, message: dict[str, Any]) -> None:
'''
panel = replace_once(panel, handler_marker, handler_replacement, label="message safety wrapper")
panel_path.write_text(panel, encoding="utf-8")

smoke_path = ROOT / "scripts" / "telegram_start_state_smoke.py"
smoke_path.write_text('''from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from admin_bot import Snapshot
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41
from admin_panel_v2 import SnapshotUnavailableError


def _message() -> dict[str, Any]:
    return {
        "message_id": 1,
        "text": "/start",
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "first_name": "Owner"},
    }


def _set_owner_context(panel: TelegramPanelRuntimeV41) -> None:
    def set_context(chat_id: Any, user_id: Any) -> None:
        panel.current_chat_id = str(chat_id)
        panel.current_user_id = str(user_id)
        panel.current_role = "owner"

    panel.set_context = set_context  # type: ignore[method-assign]


def verify_start_success() -> None:
    panel = TelegramPanelRuntimeV41()
    _set_owner_context(panel)
    calls: list[tuple[str, Any]] = []
    panel.register_user = lambda message: "owner"  # type: ignore[method-assign]
    panel.can_view = lambda: True  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel.handle_message(_message())
    assert calls == [("menu", True)]


def verify_start_failure_is_visible() -> None:
    panel = TelegramPanelRuntimeV41()
    _set_owner_context(panel)
    sent: list[str] = []

    def fail_registration(message: dict[str, Any]) -> str:
        raise RuntimeError("simulated access read failure")

    panel.register_user = fail_registration  # type: ignore[method-assign]
    panel.send = lambda text, **kwargs: sent.append(str(text)) or {}  # type: ignore[method-assign]
    panel.handle_message(_message())
    assert sent
    assert "данные не обнулены" in sent[-1]


def verify_failed_refresh_keeps_verified_snapshot() -> None:
    panel = TelegramPanelRuntimeV41()
    existing = Snapshot(
        state={"active_wheels": {"wheel": {"identifier": "wheel"}}},
        stats={"daily": {"2026-07-25": {"totals": {"checks": 17}}}, "sources": {}},
        health={"sources": {"source": {"status": "ok"}}},
        discovery={},
        unknown={"samples": []},
        fast=["source"],
        nightly=[],
    )
    panel.snapshot_value = existing

    def fail_read(path: str) -> str:
        raise RuntimeError(f"simulated read failure: {path}")

    panel._direct_get_file = fail_read  # type: ignore[method-assign]
    refreshed = panel.refresh_snapshot()
    assert refreshed is existing
    assert len(refreshed.fast) == 1
    assert len(refreshed.state["active_wheels"]) == 1


def verify_initial_failure_is_not_zero_state() -> None:
    panel = TelegramPanelRuntimeV41()

    def fail_read(path: str) -> str:
        raise RuntimeError(f"simulated read failure: {path}")

    panel._direct_get_file = fail_read  # type: ignore[method-assign]
    try:
        panel.refresh_snapshot()
    except SnapshotUnavailableError:
        return
    raise AssertionError("initial critical read failure must not become an all-zero snapshot")


def verify_populated_snapshot_remains_populated() -> None:
    panel = TelegramPanelRuntimeV41()
    values = {
        "state.json": json.dumps({"active_wheels": {"wheel": {"identifier": "wheel"}}}),
        "source_stats.json": json.dumps({"sources": {"source": {"checks": 5}}, "daily": {}}),
        "source_health.json": json.dumps({"sources": {"source": {"status": "ok"}}}),
        "discovery_state.json": "{}",
        "unknown_timer_samples.json": json.dumps({"samples": []}),
        "public_sources.txt": "source\n",
        "source_catalog.txt": "reserve\n",
    }
    panel._direct_get_file = lambda path: values[path]  # type: ignore[method-assign]
    snap = panel.refresh_snapshot()
    assert len(snap.fast) == 1
    assert len(snap.nightly) == 1
    assert len(snap.state["active_wheels"]) == 1
    assert snap.stats["sources"]["source"]["checks"] == 5


def run_smoke() -> None:
    verify_start_success()
    verify_start_failure_is_visible()
    verify_failed_refresh_keeps_verified_snapshot()
    verify_initial_failure_is_not_zero_state()
    verify_populated_snapshot_remains_populated()
    print("Telegram /start and non-zero state safety smoke passed")


if __name__ == "__main__":
    run_smoke()
''', encoding="utf-8")

test_path = ROOT / "tests" / "test_telegram_start_state_safety.py"
test_path.write_text('''from __future__ import annotations

from pathlib import Path

from scripts.telegram_start_state_smoke import run_smoke


def test_telegram_start_and_state_smoke() -> None:
    run_smoke()


def test_release_validation_runs_telegram_start_state_smoke() -> None:
    validation = Path("scripts/validate_control_center.sh").read_text(encoding="utf-8")
    assert "python scripts/telegram_start_state_smoke.py" in validation


def test_snapshot_failures_cannot_be_replaced_with_empty_strings() -> None:
    source = Path("admin_panel_v2.py").read_text(encoding="utf-8")
    refresh = source.split("def refresh_snapshot", 1)[1].split("def snapshot", 1)[0]
    assert 'values[key] = ""' not in refresh
    assert "SnapshotUnavailableError" in refresh
    assert "return current" in refresh
''', encoding="utf-8")

validation_path = ROOT / "scripts" / "validate_control_center.sh"
validation = validation_path.read_text(encoding="utf-8")
validation = replace_once(
    validation,
    'validation_stage="runtime_v41_self_test"\npython admin_panel_runtime_v41.py --self-test\n',
    'validation_stage="runtime_v41_self_test"\npython admin_panel_runtime_v41.py --self-test\nvalidation_stage="telegram_start_state_smoke"\npython scripts/telegram_start_state_smoke.py\n',
    label="release smoke insertion",
)
validation_path.write_text(validation, encoding="utf-8")

policy_path = ROOT / "engineering" / "PRODUCTION_STABILITY_POLICY_RU.md"
policy = policy_path.read_text(encoding="utf-8")
policy_marker = "## Telegram: обязательная проверка запуска и данных"
if policy_marker not in policy:
    policy += '''\n\n## Telegram: обязательная проверка запуска и данных\n\n- Команда `/start` обязана либо открыть меню, либо отправить понятное сообщение о временной недоступности; молчаливое исключение запрещено.\n- Ошибка чтения `state.json`, `source_stats.json`, `source_health.json` или `public_sources.txt` не считается пустым состоянием.\n- При временной ошибке сохраняется последний подтверждённый snapshot; если его ещё нет, запуск явно сообщает о недоступности данных.\n- Каждый кандидат Control Center до выпуска выполняет `scripts/telegram_start_state_smoke.py`.\n'''
policy_path.write_text(policy, encoding="utf-8")

changelog_path = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
changelog = changelog_path.read_text(encoding="utf-8")
changelog_marker = "### Защита Telegram от ложных нулей и молчаливого `/start`"
if changelog_marker not in changelog:
    changelog += '''\n\n### Защита Telegram от ложных нулей и молчаливого `/start`\n\n- при ошибке чтения критических файлов Telegram больше не заменяет реальные данные пустыми словарями и списками;\n- последний подтверждённый snapshot сохраняется до восстановления GitHub-чтения;\n- первый запуск без доступного snapshot завершается явной ошибкой вместо экрана с нулями;\n- исключение регистрации или загрузки доступа при `/start` теперь даёт пользователю понятный ответ;\n- release validation дополнена отдельным smoke-тестом команды `/start` и ненулевого состояния.\n'''
changelog_path.write_text(changelog, encoding="utf-8")

print("Telegram start and state safety patch prepared")
