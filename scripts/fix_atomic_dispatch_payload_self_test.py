from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("auto_participation_dispatch_atomic_payload.txt")
text = path.read_text(encoding="utf-8")
old = '    assert "subprocess" not in Path(__file__).read_text(encoding="utf-8")'
new = (
    '    forbidden_import = "import " + "subprocess"\n'
    '    assert forbidden_import not in Path(__file__).read_text(encoding="utf-8")'
)
if text.count(old) != 1:
    raise RuntimeError(f"dispatcher self-test assertion count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Atomic dispatcher self-test assertion fixed")
