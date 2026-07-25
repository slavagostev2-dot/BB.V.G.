from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("apply_navigation_ownership_consolidation.py")
text = path.read_text(encoding="utf-8")

anchor = 'interface = interface_path.read_text(encoding="utf-8")\n'
insertion = '''interface = interface_path.read_text(encoding="utf-8")
interface = replace_once(
    interface,
    '                {"text": "✅ Состояние системы", "callback_data": "page:status"},\\n',
    '                {"text": "✅ Проверить работу системы", "callback_data": "page:status"},\\n',
    label="preserve production control button label",
)
'''
if text.count(anchor) != 1:
    raise RuntimeError("interface anchor mismatch")
text = text.replace(anchor, insertion, 1)

anchor = '    assert "page:status" in callbacks\n'
insertion = '''    assert "page:status" in callbacks
    labels = {
        str(button.get("text") or "")
        for row in markup.get("inline_keyboard", [])
        for button in row
        if isinstance(button, dict)
    }
    assert "✅ Проверить работу системы" in labels
'''
if text.count(anchor) != 1:
    raise RuntimeError("control self-test anchor mismatch")
text = text.replace(anchor, insertion, 1)

path.write_text(text, encoding="utf-8")
print("Navigation patch v2 prepared")
