from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("prepare_atomic_dispatch_patch_v2.py")
text = path.read_text(encoding="utf-8")
old_start = "replacement = r'''dispatch_path"
new_start = 'replacement = r"""dispatch_path'
old_end = "\n'''\n\npattern = re.compile("
new_end = '\n"""\n\npattern = re.compile('
if text.count(old_start) != 1:
    raise RuntimeError(f"outer start delimiter count={text.count(old_start)}")
if text.count(old_end) != 1:
    raise RuntimeError(f"outer end delimiter count={text.count(old_end)}")
text = text.replace(old_start, new_start, 1).replace(old_end, new_end, 1)
path.write_text(text, encoding="utf-8")
print("Atomic dispatch generator quote delimiters fixed")
