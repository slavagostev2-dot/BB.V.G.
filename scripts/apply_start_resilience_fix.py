from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: {label}; expected one match, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


panel = ROOT / "admin_panel_v2.py"
replace_once(
    panel,
    'CACHE_REFRESH_SECONDS = max(10, int(os.getenv("ADMIN_CACHE_SECONDS", "20")))\n',
    'CACHE_REFRESH_SECONDS = max(10, int(os.getenv("ADMIN_CACHE_SECONDS", "20")))\n'
    'ACCESS_SAVE_RETRY_SECONDS = max(5, int(os.getenv("ACCESS_SAVE_RETRY_SECONDS", "15")))\n',
    "add deferred access retry interval",
)
replace_once(
    panel,
    '''        self.access = default_access()
        self.access_loaded = False
''',
    '''        self.access = default_access()
        self.access_loaded = False
        self._pending_access_save_message: str | None = None
        self._pending_access_save_retry_at = 0.0
''',
    "initialize deferred access persistence",
)
replace_once(
    panel,
    '''    def save_access(self, message: str = "Update Telegram panel access") -> None:
''',
    '''    def queue_access_save(self, message: str) -> None:
        """Keep UI handling independent from a transient GitHub write failure."""

        with self.access_lock:
            self._pending_access_save_message = str(message or "Update Telegram panel access [skip ci]")
            self._pending_access_save_retry_at = 0.0
        self.refresh_requested.set()

    def flush_pending_access_save(self) -> bool:
        """Persist queued profile metadata outside the Telegram request path."""

        now = time.monotonic()
        with self.access_lock:
            message = self._pending_access_save_message
            retry_at = self._pending_access_save_retry_at
        if not message:
            return True
        if retry_at and now < retry_at:
            return False
        try:
            self.save_access(message)
        except Exception as exc:
            with self.access_lock:
                if self._pending_access_save_message == message:
                    self._pending_access_save_retry_at = (
                        time.monotonic() + ACCESS_SAVE_RETRY_SECONDS
                    )
            print(
                "WARNING deferred Telegram profile persistence: "
                f"{type(exc).__name__}: {exc}"
            )
            return False
        with self.access_lock:
            if self._pending_access_save_message == message:
                self._pending_access_save_message = None
                self._pending_access_save_retry_at = 0.0
        return True

    def save_access(self, message: str = "Update Telegram panel access") -> None:
''',
    "add deferred access persistence methods",
)
replace_once(
    panel,
    '''        if changed:
            self.save_access("Register Telegram panel user [skip ci]")
        return self.role_for(user_id)
''',
    '''        if changed:
            self.queue_access_save("Register Telegram panel user [skip ci]")
        return self.role_for(user_id)
''',
    "remove remote write from base registration request path",
)
replace_once(
    panel,
    '''    def refresh_loop(self) -> None:
        while not self.stop_refresh.is_set():
            try:
                self.refresh_snapshot()
            except Exception as exc:
                print(f"WARNING refresh loop: {type(exc).__name__}: {exc}")
            self.refresh_requested.wait(CACHE_REFRESH_SECONDS)
            self.refresh_requested.clear()
''',
    '''    def refresh_loop(self) -> None:
        while not self.stop_refresh.is_set():
            self.flush_pending_access_save()
            try:
                self.refresh_snapshot()
            except Exception as exc:
                print(f"WARNING refresh loop: {type(exc).__name__}: {exc}")
            self.refresh_requested.wait(CACHE_REFRESH_SECONDS)
            self.refresh_requested.clear()
''',
    "flush deferred access persistence in background loop",
)

users = ROOT / "bbvg" / "bot" / "users.py"
replace_once(
    users,
    '''        if user_id and not known_user:
            access = self.load_access(force=True)
            users = access.get("users") if isinstance(access.get("users"), dict) else {}
            known_user = user_id in users

''',
    '''        # Registration must never wait for a remote encrypted-state refresh.
        # The conflict-safe background merge will reconcile concurrent users.

''',
    "remove forced remote read from user registration",
)
replace_once(
    users,
    '''        if changed:
            record["notification_preferences"] = prefs
            self.save_access(
                f"Enable wheel reminder notifications for Telegram user {user_id} [skip ci]"
            )
        return role
''',
    '''        if changed:
            record["notification_preferences"] = prefs
            self.queue_access_save(
                f"Enable wheel reminder notifications for Telegram user {user_id} [skip ci]"
            )
        return role
''',
    "remove remote write from notification default registration",
)

smoke = ROOT / "scripts" / "telegram_start_state_smoke.py"
replace_once(
    smoke,
    '''def verify_start_failure_is_visible() -> None:
''',
    '''def verify_start_profile_write_is_deferred() -> None:
    panel = TelegramPanelRuntimeV41()
    _set_owner_context(panel)
    panel.access = panel.normalize_access(
        {
            "owner_id": "1",
            "users": {
                "1": {
                    "id": "1",
                    "chat_id": "1",
                    "first_name": "Owner",
                    "first_seen_at": "2026-07-25T00:00:00+00:00",
                    "last_seen_at": "2026-07-25T00:00:00+00:00",
                    "notification_preferences": {
                        "wheel_final_reminders": True,
                        "wheel_draw_alerts": False,
                    },
                }
            },
        }
    )
    panel.access_loaded = True
    panel.load_access = lambda force=False: panel.access  # type: ignore[method-assign]
    panel.role_for = lambda user_id: "owner"  # type: ignore[method-assign]
    panel.notify_owner_about_new_user = lambda user_id: None  # type: ignore[method-assign]
    queued: list[str] = []
    calls: list[tuple[str, Any]] = []
    panel.queue_access_save = lambda message: queued.append(str(message))  # type: ignore[method-assign]
    panel.can_view = lambda: True  # type: ignore[method-assign]
    panel.show_menu = lambda clear_stack=True: calls.append(("menu", clear_stack))  # type: ignore[method-assign]
    panel.handle_message(_message())
    assert calls == [("menu", True)]
    assert queued == ["Register Telegram panel user [skip ci]"]


def verify_deferred_profile_write_retries() -> None:
    panel = TelegramPanelRuntimeV41()
    panel.queue_access_save("Register Telegram panel user [skip ci]")

    def fail_save(message: str) -> None:
        raise RuntimeError(f"simulated remote write failure: {message}")

    panel.save_access = fail_save  # type: ignore[method-assign]
    assert panel.flush_pending_access_save() is False
    assert panel._pending_access_save_message is not None
    panel._pending_access_save_retry_at = 0.0
    saved: list[str] = []
    panel.save_access = lambda message: saved.append(str(message))  # type: ignore[method-assign]
    assert panel.flush_pending_access_save() is True
    assert saved == ["Register Telegram panel user [skip ci]"]
    assert panel._pending_access_save_message is None


def verify_start_failure_is_visible() -> None:
''',
    "add deferred /start persistence smoke",
)
replace_once(
    smoke,
    '''def run_smoke() -> None:
    verify_start_success()
    verify_start_failure_is_visible()
''',
    '''def run_smoke() -> None:
    verify_start_success()
    verify_start_profile_write_is_deferred()
    verify_deferred_profile_write_retries()
    verify_start_failure_is_visible()
''',
    "run deferred /start persistence smoke",
)

tests = ROOT / "tests" / "test_telegram_start_state_safety.py"
text = tests.read_text(encoding="utf-8")
addition = '''\n\ndef test_start_profile_persistence_is_outside_request_path() -> None:\n    panel_source = Path("admin_panel_v2.py").read_text(encoding="utf-8")\n    base_registration = panel_source.split("def register_user", 1)[1].split("def can_view", 1)[0]\n    refresh = panel_source.split("def refresh_loop", 1)[1].split("# ---------- Navigation", 1)[0]\n    user_source = Path("bbvg/bot/users.py").read_text(encoding="utf-8")\n    management_registration = user_source.split("class UserManagementRuntime", 1)[1].split("def handle_update", 1)[0]\n    settings_registration = user_source.split("class UserSettingsMixin", 1)[1].split("def show_settings", 1)[0]\n    assert "queue_access_save" in base_registration\n    assert "self.save_access" not in base_registration\n    assert "load_access(force=True)" not in management_registration\n    assert "queue_access_save" in settings_registration\n    assert "self.save_access" not in settings_registration\n    assert "flush_pending_access_save()" in refresh\n    assert "ACCESS_SAVE_RETRY_SECONDS" in panel_source\n'''
if "test_start_profile_persistence_is_outside_request_path" not in text:
    tests.write_text(text.rstrip() + addition + "\n", encoding="utf-8")

changelog = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
entry = '''## 2026-07-25 — `/start` отделён от записи служебного профиля\n\nОткрытие панели больше не зависит от синхронного чтения или записи encrypted state\nв GitHub. Регистрация и обновление профиля сначала применяются к уже загруженному\nлокальному состоянию, меню показывается сразу, а зашифрованный bundle сохраняется\nфоновым циклом с повтором после временного конфликта, rate limit или сетевой ошибки.\n\nPending-запись не очищается при неудаче и повторяется с ограниченным интервалом.\nКритические ошибки первоначального чтения состояния по-прежнему видимы пользователю\nи никогда не заменяются пустыми данными. Добавлен release-smoke, который проверяет\nоткрытие меню при отложенной записи и сохранение pending после неудачного retry.\n\n**Backup перед изменением:**\n`backup/before-start-resilience-fix-20260725`.\n\n'''
text = changelog.read_text(encoding="utf-8")
if entry.splitlines()[0] not in text:
    marker = "---\n\n"
    if marker not in text:
        raise RuntimeError("changelog insertion marker not found")
    changelog.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")

print("/start resilience patch applied")
