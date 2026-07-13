from __future__ import annotations

import base64
import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD_DIR = ROOT / ".admin_v2_payload"
TARGET = ROOT / "admin_panel_v2.py"


def main() -> None:
    payload = "".join(
        (PAYLOAD_DIR / f"part{index:02d}.txt").read_text(encoding="utf-8").strip()
        for index in range(1, 7)
    )
    source = gzip.decompress(base64.b64decode(payload))
    TARGET.write_bytes(source)
    print(f"Installed {TARGET.name}: {len(source)} bytes")


if __name__ == "__main__":
    main()
