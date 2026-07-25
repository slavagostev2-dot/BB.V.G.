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


# The existing cross-cutting personal-voting mixin is the actual first MRO owner.
replace_once(
    "personal_wheel_voting.py",
    '''    def handle_callback(self, query: dict[str, Any]) -> None:
''',
    '''    @staticmethod
    def _notification_token(key: str, entry: dict[str, Any]) -> str:
        normalized = _clean_wheel_key(
            key or entry.get("wheel_key") or entry.get("identifier")
        )
        source = str(entry.get("source") or "").strip().casefold()
        try:
            message_id = int(entry.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        if not normalized or not source or message_id <= 0:
            return ""
        raw = f"{source}:{message_id}:{normalized}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]

    def _notification_wheel_key(self, token: str) -> str:
        snap = self.snapshot(force=True)
        state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
        context = state.get("button_contexts", {}).get(token)
        if isinstance(context, dict):
            key = _clean_wheel_key(
                context.get("wheel_key") or context.get("identifier")
            )
            if key:
                return key
            raise ValueError("Не удалось определить колесо")

        matches: list[str] = []
        active = state.get("active_wheels")
        if isinstance(active, dict):
            for raw_key, raw in active.items():
                if not isinstance(raw, dict):
                    continue
                key = _clean_wheel_key(raw_key)
                stored = str(raw.get("button_token") or "")
                computed = self._notification_token(key, raw)
                if token and token in {stored, computed}:
                    matches.append(key)
        unique = sorted(set(matches))
        if len(unique) != 1:
            raise ValueError("Контекст кнопки устарел")
        return unique[0]

    def handle_callback(self, query: dict[str, Any]) -> None:
''',
)

replace_once(
    "personal_wheel_voting.py",
    '''        if data.startswith(("bb:p:", "wheel:part:")):
            self._prepare_callback_user(query)
            try:
                if data.startswith("bb:p:"):
                    token = data.split(":", 2)[2]
                    context = self.snapshot(force=True).state.get("button_contexts", {}).get(token)
                    if not isinstance(context, dict):
                        raise ValueError("Контекст кнопки устарел")
                    key = _clean_wheel_key(
                        context.get("wheel_key") or context.get("identifier")
                    )
                else:
                    token = data.split(":", 2)[2]
                    key = self._resolve_wheel_token(token) or ""
                result = self.mark_personal_participation(key)
            except Exception as exc:
                print(f"ERROR personal wheel vote: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось отметить участие")
                return
            self.answer(
                query_id,
                "Участие уже было отмечено" if not result.get("changed") else "Ваше участие отмечено",
            )
            try:
                self.show_active()
            except Exception:
                pass
            return
''',
    '''        if data.startswith(("bb:p:", "wheel:part:")):
            self._prepare_callback_user(query)
            notification_button = data.startswith("bb:p:")
            message = query.get("message") if isinstance(query, dict) else None
            message = message if isinstance(message, dict) else {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            if not notification_button:
                self._edit_message_id = int(message.get("message_id") or 0) or None
            try:
                token = data.split(":", 2)[2]
                key = (
                    self._notification_wheel_key(token)
                    if notification_button
                    else self._resolve_wheel_token(token) or ""
                )
                result = self.mark_personal_participation(key)
                self.answer(
                    query_id,
                    "Участие уже было отмечено"
                    if not result.get("changed")
                    else "Ваше участие отмечено",
                )
                if notification_button:
                    self._delete_callback_message(query)
                else:
                    self.show_active()
            except Exception as exc:
                print(f"ERROR personal wheel vote: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось отметить участие")
            finally:
                if not notification_button:
                    self._edit_message_id = previous_edit_message_id
            return
''',
)

# Compatibility v41 no longer intercepts personal wheel callbacks.
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
replace_once(
    "admin_panel_runtime_v41.py",
    '''    panel.mark_personal_participation = lambda key: events.append(("participate", str(key)))  # type: ignore[method-assign]
''',
    '''    panel.mark_personal_participation = lambda key: (  # type: ignore[method-assign]
        events.append(("participate", str(key))),
        {"changed": True},
    )[1]
''',
)

# Production entrypoint remains compatible but has no callback-specific implementation.
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
    """Compatibility entrypoint; personal wheel callbacks have one subject owner."""
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

# Freeze the actual MRO owner and prevent the compatibility overrides from returning.
replace_once(
    "tests/test_production_stability_guardrails.py",
    '''def test_historical_panel_runtime_ladder_cannot_return() -> None:
''',
    '''def test_wheel_participation_callbacks_have_one_subject_owner() -> None:
    owner = (ROOT / "personal_wheel_voting.py").read_text(encoding="utf-8")
    runtime_v41 = (ROOT / "admin_panel_runtime_v41.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "notification_button_recovery.py").read_text(
        encoding="utf-8"
    )
    assert "def _notification_wheel_key" in owner
    assert 'if data.startswith(("bb:p:", "wheel:part:"))' in owner
    assert "def _mark_personal_from_notification" not in runtime_v41
    assert 'if data.startswith("bb:p:")' not in runtime_v41
    assert 'if data.startswith("wheel:part:")' not in runtime_v41
    assert "def _mark_personal_from_notification" not in entrypoint
    assert "def _notification_token" not in entrypoint


def test_historical_panel_runtime_ladder_cannot_return() -> None:
''',
)

replace_once(
    "engineering/REFACTOR_PLAN_RU.md",
    '''## Оставшийся технический долг
''',
    '''## Выполнено 25 июля 2026 года — первый этап стабильного упрощения

- фактическим единственным владельцем `bb:p:<token>` и `wheel:part:<key>` закреплён
  существующий `PersonalWheelVotingMixin` в `personal_wheel_voting.py`;
- восстановление потерянного `button_contexts` перенесено к тому же владельцу;
- из `admin_panel_runtime_v41.py` удалены две отдельные callback-ветки;
- из `notification_button_recovery.py` удалены собственные token-helper и override;
- удаление исходной карточки уведомления, редактирование сообщения «Активные колёса»,
  event-scoped личный голос и прежние callback-строки сохранены.

## Оставшийся технический долг
''',
)

print("Control Center callback consolidation patch applied to actual MRO owner")
