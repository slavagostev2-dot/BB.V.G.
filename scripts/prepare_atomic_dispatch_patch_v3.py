from __future__ import annotations

import re
from pathlib import Path

path = Path(__file__).with_name("apply_atomic_auto_participation_dispatch.py")
text = path.read_text(encoding="utf-8")
replacement = '''dispatch_path = ROOT / "auto_participation_dispatch.py"
payload_path = ROOT / "scripts" / "auto_participation_dispatch_atomic_payload.txt"
dispatch_path.write_text(payload_path.read_text(encoding="utf-8"), encoding="utf-8")
'''
pattern = re.compile(
    r'dispatch_path = ROOT / "auto_participation_dispatch\.py".*?dispatch_path\.write_text\(dispatch, encoding="utf-8"\)\n',
    re.DOTALL,
)
updated, count = pattern.subn(lambda _match: replacement, text, count=1)
if count != 1:
    raise RuntimeError(f"dispatcher generator block count={count}")
path.write_text(updated, encoding="utf-8")
print("Atomic dispatch generator switched to payload copy")
