from __future__ import annotations

from pathlib import Path

consistency_path = Path("tests/test_auto_participation_consistency.py")
consistency = consistency_path.read_text(encoding="utf-8")
old_order = '    assert calls == ["send", "save", "dispatch"]'
new_order = '    assert calls == ["save", "dispatch", "send"]'
if old_order in consistency:
    consistency = consistency.replace(old_order, new_order, 1)
elif new_order not in consistency:
    raise RuntimeError("notification order assertion not found")
consistency_path.write_text(consistency, encoding="utf-8")

recurring_path = Path("tests/test_recurring_event_hotfix.py")
recurring = recurring_path.read_text(encoding="utf-8")
region_start = recurring.index(
    "    def test_scheduled_availability_preserves_server_generation"
)
region_end = recurring.index(
    "    def test_notification_dedup_is_scoped",
    region_start,
)
region = recurring[region_start:region_end]
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
if old_fake in region:
    region = region.replace(old_fake, new_fake, 1)
elif "def dispatch_notified_wheel_event(state, link):" not in region:
    raise RuntimeError("scheduled availability FakeMonitor target not found")

old_assert = '        self.assertEqual(captured["server_start_at"], server_start)'
new_assert = '''        self.assertEqual(captured["server_start_at"], server_start)
        self.assertEqual(
            captured["dispatched_link"],
            "https://betboom.ru/freestream/reused",
        )
        self.assertTrue(captured["sent"])
'''
if 'captured["dispatched_link"]' not in region:
    if region.count(old_assert) != 1:
        raise RuntimeError(f"focused server assertion count={region.count(old_assert)}")
    region = region.replace(old_assert, new_assert, 1)
recurring = recurring[:region_start] + region + recurring[region_end:]
recurring_path.write_text(recurring, encoding="utf-8")

agents_path = Path("AGENTS.md")
agents = agents_path.read_text(encoding="utf-8")
old_contract = (
    "После успешной первичной доставки точный `wheel_key + action_id + "
    "server_start_at` сохраняется в `state.json` и немедленно передаётся "
    "единственному dispatcher; post-scan вызов остаётся страховкой."
)
new_contract = (
    "До внешней первичной доставки точный `wheel_key + action_id + "
    "server_start_at` и dispatch-outbox сохраняются в `state.json` через "
    "file-level CAS с объединением последнего remote-state; только после этого "
    "отправляется карточка, а post-scan вызов остаётся страховкой. Dispatcher "
    "не выполняет `git pull --rebase` в рабочем каталоге живого Monitor."
)
if old_contract in agents:
    agents = agents.replace(old_contract, new_contract, 1)
elif new_contract not in agents:
    raise RuntimeError("AGENTS notification/dispatch contract not found")
agents_path.write_text(agents, encoding="utf-8")

print("Atomic dispatch regression expectations updated")
