from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("apply_telegram_start_state_safety.py")
text = path.read_text(encoding="utf-8")
replacements = {
    '"⚠️ <b>Панель временно не смогла загрузить данные.</b>\\n\\n"': '"⚠️ <b>Панель временно не смогла загрузить данные.</b>\\\\n\\\\n"',
    '"public_sources.txt": "source\\n",': '"public_sources.txt": "source\\\\n",',
    '"source_catalog.txt": "reserve\\n",': '"source_catalog.txt": "reserve\\\\n",',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one escaping target for {old!r}, got {count}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("Telegram safety patch escaping fixed")
