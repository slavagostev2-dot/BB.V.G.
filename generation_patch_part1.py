from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"marker missing in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str, minimum: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"expected {minimum}+ markers in {path}, got {count}")
    p.write_text(text.replace(old, new), encoding="utf-8")


# Preserve authoritative BetBoom start_dttm through inspection and assessment.
replace_all(
    "monitor.py",
    '    verification_status: str = ""\n',
    '    verification_status: str = ""\n    server_start_at: datetime | None = None\n',
    2,
)
replace_all(
    "monitor.py",
    '        "available_at": inspection.available_at,\n        "verification_status": inspection.verification_status,\n',
    '        "available_at": inspection.available_at,\n        "server_start_at": inspection.server_start_at,\n        "verification_status": inspection.verification_status,\n',
    2,
)
replace_once(
    "monitor.py",
    '            action_id=action_id,\n            verification_status=WHEEL_VERIFICATION_CONFIRMED,\n        )\n    if deadline is not None and deadline <= reference:\n',
    '            action_id=action_id,\n            server_start_at=start,\n            verification_status=WHEEL_VERIFICATION_CONFIRMED,\n        )\n    if deadline is not None and deadline <= reference:\n',
)
replace_once(
    "monitor.py",
    '            action_id=action_id,\n            verification_status=WHEEL_VERIFICATION_CONFIRMED,\n        )\n\n    available_at = start if start and start > reference else None\n',
    '            action_id=action_id,\n            server_start_at=start,\n            verification_status=WHEEL_VERIFICATION_CONFIRMED,\n        )\n\n    available_at = start if start and start > reference else None\n',
)
replace_once(
    "monitor.py",
    '        action_id=action_id,\n        available_at=available_at,\n        verification_status=WHEEL_VERIFICATION_CONFIRMED,\n    )\n',
    '        action_id=action_id,\n        available_at=available_at,\n        server_start_at=start,\n        verification_status=WHEEL_VERIFICATION_CONFIRMED,\n    )\n',
)

# Active persistence carries the generation fields.
replace_once(
    "monitor.py",
    '    verification_status: str = "",\n) -> None:\n    current = now_utc()\n    key = wheel_key(link)\n',
    '    verification_status: str = "",\n    server_start_at: datetime | None = None,\n) -> None:\n    current = now_utc()\n    key = wheel_key(link)\n',
)
replace_once(
    "monitor.py",
    '        state.setdefault("wheel_action_history", {})[key] = {\n            "action_id": action_id,\n            "seen_at": current.isoformat(),\n        }\n',
    '        history_entry = {"action_id": action_id, "seen_at": current.isoformat()}\n        if server_start_at is not None:\n            history_entry["server_start_at"] = server_start_at.astimezone(UTC).isoformat()\n        state.setdefault("wheel_action_history", {})[key] = history_entry\n',
)
replace_once(
    "monitor.py",
    '    if available_at is not None:\n        entry["available_at"] = available_at.isoformat()\n',
    '    if server_start_at is not None:\n        entry["server_start_at"] = server_start_at.astimezone(UTC).isoformat()\n    if available_at is not None:\n        entry["available_at"] = available_at.isoformat()\n',
)

# Notification helpers pass the server start to remember_active_wheel.
text = Path("monitor.py").read_text(encoding="utf-8")
text = text.replace(
    '    verification_status: str = "",\n) -> None:\n    identifier_raw = wheel_identifier(link)\n',
    '    verification_status: str = "",\n    server_start_at: datetime | None = None,\n) -> None:\n    identifier_raw = wheel_identifier(link)\n',
)
text = text.replace(
    '            verification_status=verification_status,\n        )\n',
    '            verification_status=verification_status,\n            server_start_at=server_start_at,\n        )\n',
)
# Every main-loop handoff from WheelAssessment keeps the field.
if "server_start_at=assessment.server_start_at" not in text:
    text = re.sub(
        r'(?m)^(\s*)verification_status=assessment\.verification_status,\n',
        r'\1verification_status=assessment.verification_status,\n\1server_start_at=assessment.server_start_at,\n',
        text,
    )
Path("monitor.py").write_text(text, encoding="utf-8")

# Wrapper reconstructions must not drop the generation metadata.
replace_once(
    "monitor_entry.py",
    '            verification_status=result.verification_status,\n        )\n',
    '            verification_status=result.verification_status,\n            server_start_at=result.server_start_at,\n        )\n',
)
replace_all(
    "wheel_link_lifecycle.py",
    '                verification_status=result.verification_status,\n            )\n',
    '                verification_status=result.verification_status,\n                server_start_at=result.server_start_at,\n            )\n',
    2,
)
