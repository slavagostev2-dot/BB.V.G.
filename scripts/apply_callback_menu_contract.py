from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(
            f"Expected exactly one menu-contract target in {path}, found {text.count(old)}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "personal_wheel_voting.py",
    '''                if notification_button:
                    self._delete_callback_message(query)
                else:
                    self.show_active()
''',
    '''                if notification_button:
                    self._delete_callback_message(query)
                else:
                    self.show_menu(clear_stack=True)
''',
)

replace_once(
    "bbvg/bot/runtime.py",
    '''    panel.show_active = lambda page=0: callback_calls.append(("active", page))  # type: ignore[method-assign]
''',
    '''    panel.show_menu = lambda clear_stack=True: callback_calls.append(("menu", clear_stack))  # type: ignore[method-assign]
''',
)
replace_once(
    "bbvg/bot/runtime.py",
    '''    assert not any(event[0] == "active" for event in callback_calls)
''',
    '''    assert not any(event[0] == "menu" for event in callback_calls)
''',
)
replace_once(
    "bbvg/bot/runtime.py",
    '''    assert ("active", 0) in callback_calls
''',
    '''    assert ("menu", True) in callback_calls
''',
)

replace_once(
    "admin_panel_runtime_v41.py",
    '''    panel.show_active = lambda page=0: events.append(("active", str(page)))  # type: ignore[method-assign]
''',
    '''    panel.show_menu = lambda clear_stack=True: events.append(("menu", str(clear_stack)))  # type: ignore[method-assign]
''',
)
replace_once(
    "admin_panel_runtime_v41.py",
    '''    assert ("active", "0") in events
''',
    '''    assert ("menu", "True") in events
''',
)

replace_once(
    "tests/test_production_stability_guardrails.py",
    '''    panel.show_active = lambda page=0: events.append(("active", page))  # type: ignore[method-assign]
''',
    '''    panel.show_menu = lambda clear_stack=True: events.append(("menu", clear_stack))  # type: ignore[method-assign]
''',
)
replace_once(
    "tests/test_production_stability_guardrails.py",
    '''    assert not any(name == "active" for name, _ in events)
''',
    '''    assert not any(name == "menu" for name, _ in events)
''',
)
replace_once(
    "tests/test_production_stability_guardrails.py",
    '''    assert ("active", 0) in events
''',
    '''    assert ("menu", True) in events
''',
)

replace_once(
    "engineering/REFACTOR_PLAN_RU.md",
    '''- удаление исходной карточки уведомления, редактирование сообщения «Активные колёса»,
  event-scoped личный голос и прежние callback-строки сохранены;
''',
    '''- удаление исходной карточки уведомления, открытие главного меню в том же сообщении,
  event-scoped личный голос и прежние callback-строки сохранены;
''',
)
replace_once(
    "docs/PROJECT_CHANGELOG_RU.md",
    '''уведомления удаляет исходную карточку после отметки, а кнопка списка активных
колёс обновляет то же сообщение.
''',
    '''уведомления удаляет исходную карточку после отметки, а кнопка списка активных
колёс открывает главное меню в том же сообщении.
''',
)

print("Authoritative same-message menu contract applied")
