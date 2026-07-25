from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("apply_auto_participation_consistency.py")
text = path.read_text(encoding="utf-8")

old = '    print("auto participation notifications self-test passed")\\n'
new = '    print("unified auto participation notifications self-test passed")\\n'
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise RuntimeError("notification self-test marker not found in patch script")

anchor = '''            base: {
                "wheel_key": "wheel",
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:00+00:00",
            },
'''
replacement = '''            base: {
                "wheel_key": "wheel",
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "event_token": base,
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:00+00:00",
            },
'''
insert = f'''replace_once(
    notifications,
    {anchor!r},
    {replacement!r},
    "label existing primary self-test record",
)
replace_once(
    notifications,
    '    assert "Аккаунты: <b>1 и 2</b>" in text\\n',
    '    assert "✅ Аккаунт 1 — участие подтверждено BetBoom" in text\\n    assert "✅ Аккаунт 2 — участие подтверждено BetBoom" in text\\n',
    "update existing success message expectation",
)

'''
marker = '# Extend existing self-tests instead of creating another runtime module.\n'
if "label existing primary self-test record" not in text:
    if marker not in text:
        raise RuntimeError("self-test extension marker not found")
    text = text.replace(marker, insert + marker, 1)

path.write_text(text, encoding="utf-8")
print("auto participation patch script adjusted")
