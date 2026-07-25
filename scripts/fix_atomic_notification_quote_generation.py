from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("apply_atomic_auto_participation_dispatch.py")
text = path.read_text(encoding="utf-8")
old_monitor = '        f"Источник: <a href=\\"{html.escape(message.message_url, quote=True)}\\">"'
new_monitor = '        f\'Источник: <a href="{html.escape(message.message_url, quote=True)}">\''
old_runtime = '        f"Источник: <a href=\\"{html.escape(message.message_url, quote=True)}\\">"'
new_runtime = '        f\'Источник: <a href="{html.escape(message.message_url, quote=True)}">\''
monitor_count = text.count(old_monitor)
if monitor_count != 3:
    raise RuntimeError(f"notification href quote target count={monitor_count}")
text = text.replace(old_monitor, new_monitor)
path.write_text(text, encoding="utf-8")
print("Generated notification HTML quotes fixed")
