from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(
            f"Expected exactly one patch target in {path}, found {text.count(old)}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# One subject owner now creates and resolves notification participation tokens.
replace_once(
    "bbvg/bot/wheels.py",
    '''    @classmethod
    def _wheel_token(cls, key: str, available_bytes: int) -> str:
''',
    '''    @staticmethod
    def _notification_token(key: str, entry: dict[str, Any]) -> str:
        normalized = str(
            key or entry.get("wheel_key") or entry.get("identifier") or ""
        ).casefold()
        source = str(entry.get("source") or "").strip().casefold()
        try:
            message_id = int(entry.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        if not normalized or not source or message_id <= 0:
            return ""
        raw = f"{source}:{message_id}:{normalized}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]

    @classmethod
    def _wheel_token(cls, key: str, available_bytes: int) -> str:
''',
)

replace_once(
    "bbvg/bot/wheels.py",
    '''    def handle_callback(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        message = query.get("message") if isinstance(query, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = query.get("from") if isinstance(query, dict) else None
        self.set_context(
            chat.get("id") if isinstance(chat, dict) else None,
            sender.get("id") if isinstance(sender, dict) else None,
        )
        data = str(query.get("data") or "")
''',
    '''    def _mark_personal_from_notification(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        token = data.split(":", 2)[2]
        snap = self.snapshot()
        state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}

        context = state.get("button_contexts", {}).get(token)
        if isinstance(context, dict):
            key = str(
                context.get("wheel_key") or context.get("identifier") or ""
            ).casefold()
            if not key:
                raise ValueError("Не удалось определить колесо")
            self.mark_personal_participation(key)
            return

        matches: list[str] = []
        active = state.get("active_wheels")
        if isinstance(active, dict):
            for key, raw in active.items():
                if not isinstance(raw, dict):
                    continue
                normalized = str(key).casefold()
                stored = str(raw.get("button_token") or "")
                computed = self._notification_token(normalized, raw)
                if token and token in {stored, computed}:
                    matches.append(normalized)

        unique = sorted(set(matches))
        if len(unique) != 1:
            raise ValueError("Контекст кнопки устарел")
        self.mark_personal_participation(unique[0])

    def handle_callback(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("id") or "")
        prepare_user = getattr(self, "_prepare_callback_user", None)
        if callable(prepare_user):
            prepare_user(query)
        else:
            message = query.get("message") if isinstance(query, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            sender = query.get("from") if isinstance(query, dict) else None
            self.set_context(
                chat.get("id") if isinstance(chat, dict) else None,
                sender.get("id") if isinstance(sender, dict) else None,
            )
        data = str(query.get("data") or "")
''',
)

replace_once(
    "bbvg/bot/wheels.py",
    '''            if data.startswith("bb:p:"):
                token = data.split(":", 2)[2]
                if self.is_admin():
                    self.dispatch_admin_action("participate_token", token)
                    self.answer(query_id, "Колесо подтверждается для всех")
                else:
                    context = self.snapshot().state.get("button_contexts", {}).get(token)
                    if not isinstance(context, dict):
                        raise ValueError("Контекст кнопки устарел")
                    key = str(
                        context.get("wheel_key") or context.get("identifier") or ""
                    ).casefold()
                    self.mark_personal_participation(key)
                    self.answer(query_id, "Ваше участие отмечено")
                return
            if data.startswith("wheel:part:"):
                key = data.split(":", 2)[2]
                if self.is_admin():
                    self.dispatch_admin_action("participate_wheel", key)
                    self.answer(query_id, "Колесо подтверждается для всех")
                else:
                    self.mark_personal_participation(key)
                    self.answer(query_id, "Ваше участие отмечено")
                return
''',
    '''            if data.startswith("bb:p:"):
                self._mark_personal_from_notification(query)
                self.answer(query_id, "Ваше участие отмечено")
                self._delete_callback_message(query)
                return
            if data.startswith("wheel:part:"):
                message = query.get("message") if isinstance(query, dict) else None
                message = message if isinstance(message, dict) else {}
                previous_edit_message_id = getattr(self, "_edit_message_id", None)
                self._edit_message_id = int(message.get("message_id") or 0) or None
                try:
                    key = data.split(":", 2)[2]
                    self.mark_personal_participation(key)
                    self.answer(query_id, "Ваше участие отмечено")
                    self.show_active()
                finally:
                    self._edit_message_id = previous_edit_message_id
                return
''',
)

# The compatibility runtime no longer owns wheel-participation callbacks.
replace_once(
    "admin_panel_runtime_v41.py",
    '''    def _mark_personal_from_notification(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        token = data.split(":", 2)[2]
        context = self.snapshot().state.get("button_contexts", {}).get(token)
        if not isinstance(context, dict):
            raise ValueError("Контекст кнопки устарел")
        key = str(context.get("wheel_key") or context.get("identifier") or "").casefold()
        if not key:
            raise ValueError("Не удалось определить колесо")
        self.mark_personal_participation(key)

''',
    "",
)

replace_once(
    "admin_panel_runtime_v41.py",
    '''        if data.startswith("wheel:part:"):
            message = query.get("message") if isinstance(query, dict) else None
            message = message if isinstance(message, dict) else {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                self._prepare_callback_user(query)
                key = data.split(":", 2)[2]
                self.mark_personal_participation(key)
                self.answer(query_id, "Ваше участие отмечено")
                # Re-render the same Active Wheels message. No navigation occurs.
                self.show_active()
            except Exception as exc:
                print(f"ERROR active participation {data}: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось выполнить действие")
            finally:
                self._edit_message_id = previous_edit_message_id
            return

        if data.startswith("bb:p:"):
            try:
                self._prepare_callback_user(query)
                self._mark_personal_from_notification(query)
                self.answer(query_id, "Ваше участие отмечено")
                self._delete_callback_message(query)
            except Exception as exc:
                print(f"ERROR notification participation {data}: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось выполнить действие")
            return

''',
    "",
)

# The production entrypoint remains compatible but no longer overrides the owner method.
replace_once(
    "notification_button_recovery.py",
    "import hashlib\n",
    "",
)
replace_once(
    "notification_button_recovery.py",
    '''def _notification_token(key: str, entry: dict[str, Any]) -> str:
    normalized = str(key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
    source = str(entry.get("source") or "").strip().casefold()
    try:
        message_id = int(entry.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    if not normalized or not source or message_id <= 0:
        return ""
    raw = f"{source}:{message_id}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]


class TelegramPanelRuntimeButtonRecovery(TelegramPanelRuntimeV41):
    """Keep old notification buttons usable even if their saved context was lost."""

    def _mark_personal_from_notification(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        token = data.split(":", 2)[2]
        snap = self.snapshot()
        state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}

        context = state.get("button_contexts", {}).get(token)
        if isinstance(context, dict):
            key = str(
                context.get("wheel_key") or context.get("identifier") or ""
            ).casefold()
            if not key:
                raise ValueError("Не удалось определить колесо")
            self.mark_personal_participation(key)
            return

        matches: list[str] = []
        active = state.get("active_wheels")
        if isinstance(active, dict):
            for key, raw in active.items():
                if not isinstance(raw, dict):
                    continue
                normalized = str(key).casefold()
                stored = str(raw.get("button_token") or "")
                computed = _notification_token(normalized, raw)
                if token and token in {stored, computed}:
                    matches.append(normalized)

        unique = sorted(set(matches))
        if len(unique) != 1:
            raise ValueError("Контекст кнопки устарел")
        self.mark_personal_participation(unique[0])
''',
    '''class TelegramPanelRuntimeButtonRecovery(TelegramPanelRuntimeV41):
    """Compatibility production entrypoint; wheel callbacks belong to bbvg.bot.wheels."""
''',
)
replace_once(
    "notification_button_recovery.py",
    '''    token = _notification_token(
        "hooch07", {"source": "hoochcs2", "message_id": 2198}
    )
''',
    '''    token = panel._notification_token(
        "hooch07", {"source": "hoochcs2", "message_id": 2198}
    )
''',
)

# Guard the new ownership boundary against regression.
replace_once(
    "tests/test_production_stability_guardrails.py",
    '''def test_historical_panel_runtime_ladder_cannot_return() -> None:
''',
    '''def test_wheel_participation_callbacks_have_one_subject_owner() -> None:
    wheels = (ROOT / "bbvg/bot/wheels.py").read_text(encoding="utf-8")
    runtime_v41 = (ROOT / "admin_panel_runtime_v41.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "notification_button_recovery.py").read_text(
        encoding="utf-8"
    )
    assert "def _mark_personal_from_notification" in wheels
    assert 'if data.startswith("bb:p:")' in wheels
    assert 'if data.startswith("wheel:part:")' in wheels
    assert "def _mark_personal_from_notification" not in runtime_v41
    assert 'if data.startswith("bb:p:")' not in runtime_v41
    assert 'if data.startswith("wheel:part:")' not in runtime_v41
    assert "def _mark_personal_from_notification" not in entrypoint
    assert "def _notification_token" not in entrypoint


def test_historical_panel_runtime_ladder_cannot_return() -> None:
''',
)

# Record the completed first reduction step in the refactor plan.
replace_once(
    "engineering/REFACTOR_PLAN_RU.md",
    '''## Оставшийся технический долг
''',
    '''## Выполнено 25 июля 2026 года — первый этап стабильного упрощения

- обработка `bb:p:<token>` и `wheel:part:<key>` перенесена к предметному владельцу
  `bbvg/bot/wheels.py`;
- восстановление потерянного `button_contexts` теперь принадлежит тому же владельцу;
- из `admin_panel_runtime_v41.py` удалены две отдельные callback-ветки;
- из `notification_button_recovery.py` удалены собственные token-helper и override;
- callback-строки, удаление исходной карточки и обновление сообщения «Активные колёса»
  сохранены без изменения.

## Оставшийся технический долг
''',
)

print("Control Center callback consolidation patch applied")
