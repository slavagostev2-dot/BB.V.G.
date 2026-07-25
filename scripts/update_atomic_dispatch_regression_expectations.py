from __future__ import annotations

from pathlib import Path

consistency_path = Path("tests/test_auto_participation_consistency.py")
consistency = consistency_path.read_text(encoding="utf-8")
old_order = '    assert calls == ["send", "save", "dispatch"]'
new_order = '    assert calls == ["save", "dispatch", "send"]'
if consistency.count(old_order) != 1:
    raise RuntimeError(f"notification order assertion count={consistency.count(old_order)}")
consistency_path.write_text(consistency.replace(old_order, new_order, 1), encoding="utf-8")

recurring_path = Path("tests/test_recurring_event_hotfix.py")
recurring = recurring_path.read_text(encoding="utf-8")
old_fake = '''            @staticmethod
            def send_message(*args, **kwargs):
                return {"ok": True}

            @staticmethod
            def remember_active_wheel(state, message, link, deadline, status, method, excerpt, **kwargs):
'''
new_fake = '''            @staticmethod
            def send_message(*args, **kwargs):
                captured["sent"] = True
                return {"ok": True}

            @staticmethod
            def dispatch_notified_wheel_event(state, link):
                captured["dispatched_link"] = link
                return True

            @staticmethod
            def remember_active_wheel(state, message, link, deadline, status, method, excerpt, **kwargs):
'''
if recurring.count(old_fake) != 1:
    raise RuntimeError(f"scheduled availability fake count={recurring.count(old_fake)}")
recurring = recurring.replace(old_fake, new_fake, 1)
old_assert = '        self.assertEqual(captured["server_start_at"], server_start)'
new_assert = '''        self.assertEqual(captured["server_start_at"], server_start)
        self.assertEqual(
            captured["dispatched_link"],
            "https://betboom.ru/freestream/reused",
        )
        self.assertTrue(captured["sent"])
'''
if recurring.count(old_assert) < 1:
    raise RuntimeError("server_start_at assertion not found")
# Limit replacement to the first matching assertion in this focused test region.
region_start = recurring.index("    def test_scheduled_availability_preserves_server_generation")
region_end = recurring.index("    def test_notification_dedup_is_scoped", region_start)
region = recurring[region_start:region_end]
if region.count(old_assert) != 1:
    raise RuntimeError(f"focused server assertion count={region.count(old_assert)}")
region = region.replace(old_assert, new_assert, 1)
recurring = recurring[:region_start] + region + recurring[region_end:]
recurring_path.write_text(recurring, encoding="utf-8")

agents_path = Path("AGENTS.md")
agents = agents_path.read_text(encoding="utf-8")
marker = "- Карточка нового колеса не может быть отправлена раньше устойчивой записи exact event и dispatch-outbox."
if marker not in agents:
    anchor = "## Обязательные проверки перед выпуском"
    insertion = '''## Атомарность уведомления и автоучастия

- Карточка нового колеса не может быть отправлена раньше устойчивой записи exact event и dispatch-outbox.
- Dispatcher не должен выполнять `git pull --rebase` в рабочем каталоге живого Monitor; `state.json` публикуется file-level CAS с merge последнего remote-state.
- Повторно используемый URL различается по `action_id + server_start_at`; старая генерация не может подавлять новую.

'''
    if anchor not in agents:
        raise RuntimeError("AGENTS validation anchor not found")
    agents = agents.replace(anchor, insertion + anchor, 1)
    agents_path.write_text(agents, encoding="utf-8")

print("Atomic dispatch regression expectations updated")
