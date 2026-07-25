from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "notification_button_recovery.py"
text = path.read_text(encoding="utf-8")

replacements = (
    (
        '''    panel._mark_personal_from_notification({"data": f"bb:p:{token}"})
    assert events == ["hooch07"]
''',
        '''    resolved = panel._notification_wheel_key(token)
    assert resolved == "hooch07"
    panel.mark_personal_participation(resolved)
    assert events == ["hooch07"]
''',
    ),
    (
        '''    panel._mark_personal_from_notification({"data": "bb:p:saved"})
    assert events == ["wheel-b"]
''',
        '''    resolved = panel._notification_wheel_key("saved")
    assert resolved == "wheel-b"
    panel.mark_personal_participation(resolved)
    assert events == ["wheel-b"]
''',
    ),
)

for old, new in replacements:
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one entrypoint self-test target, found {text.count(old)}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Production entrypoint self-test updated for the single callback owner")
