from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"marker missing in {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


path = Path("wheel_event_runtime.py")
text = path.read_text(encoding="utf-8")
if "import hashlib\n" not in text:
    text = text.replace("import html\n", "import hashlib\nimport html\n", 1)

marker = "def known_action_ids(state: dict[str, Any], key: str) -> set[int]:\n"
if "def generation_id(" not in text:
    helpers = r'''def _record_action_id(record: Any) -> int | None:
    if not isinstance(record, dict):
        return None
    try:
        value = int(record.get("action_id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def generation_id(key: str, action_id: int | None, server_start_at: Any) -> str:
    """Stable identity for one authoritative BetBoom server start."""

    start = _parse_datetime(server_start_at)
    if action_id is None or start is None:
        return ""
    raw = "\x1f".join(
        (str(key or "").casefold(), str(int(action_id)), start.astimezone(UTC).isoformat())
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _generation_records(
    state: dict[str, Any], key: str
) -> list[tuple[str, dict[str, Any]]]:
    normalized = str(key or "").casefold()
    rows: list[tuple[str, dict[str, Any]]] = []
    for collection_name in (
        "active_wheels",
        "inactive_wheels",
        "recently_completed_wheels",
        "wheel_action_history",
    ):
        collection = state.get(collection_name)
        record = collection.get(normalized) if isinstance(collection, dict) else None
        if isinstance(record, dict) and _record_action_id(record) is not None:
            rows.append((collection_name, record))
    return rows


def action_generation_status(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
) -> str:
    """Return legacy/new/same/new_action/new_generation for API identity."""

    if action_id is None:
        return "legacy"
    rows = _generation_records(state, key)
    if not rows:
        return "new"
    same_action = [
        (name, record) for name, record in rows
        if _record_action_id(record) == action_id
    ]
    if not same_action:
        return "new_action"

    current_start = _parse_datetime(server_start_at)
    if current_start is None:
        return "same"
    stored_starts = [
        parsed for _, record in same_action
        if (parsed := _parse_datetime(record.get("server_start_at"))) is not None
    ]
    if any(parsed == current_start for parsed in stored_starts):
        return "same"
    if stored_starts:
        return "new_generation" if current_start > max(stored_starts) else "same"

    terminal_markers: list[datetime] = []
    for collection_name, record in same_action:
        terminal = (
            collection_name in {"inactive_wheels", "recently_completed_wheels"}
            or str(record.get("state") or "") in {"closed", "finished", "inactive"}
            or str(record.get("lifecycle_state") or "") in {"finished", "inactive"}
        )
        if not terminal:
            continue
        marker_time = _record_time(
            record,
            "closed_at",
            "removed_at",
            "confirmed_finished_at",
            "marked_at",
            "seen_at",
        )
        if marker_time is not None:
            terminal_markers.append(marker_time)
    if terminal_markers and current_start > max(terminal_markers):
        return "new_generation"
    return "same"


def record_generation_identity(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
    *,
    current: datetime,
    status: str = "active",
) -> str:
    if action_id is None:
        return ""
    normalized = str(key or "").casefold()
    start = _parse_datetime(server_start_at)
    identity = generation_id(normalized, action_id, start)
    active = state.get("active_wheels")
    entry = active.get(normalized) if isinstance(active, dict) else None
    if isinstance(entry, dict):
        entry["action_id"] = int(action_id)
        if start is not None:
            entry["server_start_at"] = start.isoformat()
        if identity:
            entry["generation_id"] = identity

    history = {
        "action_id": int(action_id),
        "seen_at": current.astimezone(UTC).isoformat(),
        "state": status,
    }
    if start is not None:
        history["server_start_at"] = start.isoformat()
    if identity:
        history["generation_id"] = identity
    if status in {"closed", "finished", "inactive"}:
        history["closed_at"] = current.astimezone(UTC).isoformat()
    state.setdefault("wheel_action_history", {})[normalized] = history
    return identity


def reset_changed_generation_state(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
    server_start_at: Any,
) -> list[str]:
    if action_id is None:
        return []
    status = action_generation_status(state, key, action_id, server_start_at)
    if status not in {"new_action", "new_generation"}:
        return []
    normalized = str(key or "").casefold()
    removed: list[str] = []
    for collection_name in (
        "active_wheels",
        "inactive_wheels",
        "recently_completed_wheels",
        "completed_wheel_alerts",
        "url_alerts",
        "activation_alerts",
        "manual_deadlines",
        "manual_overrides",
        "participating_wheels",
        "wheel_publications",
    ):
        collection = state.get(collection_name)
        if isinstance(collection, dict) and normalized in collection:
            collection.pop(normalized, None)
            removed.append(collection_name)
    return removed


'''
    if marker not in text:
        raise RuntimeError("known_action_ids marker missing")
    text = text.replace(marker, helpers + marker, 1)

# Keep legacy API but make it a compatibility wrapper for action-id changes.
start = text.index("def reset_changed_action_state(\n")
end = text.index("\n\ndef recover_recent_events_from_seen", start)
text = text[:start] + '''def reset_changed_action_state(
    state: dict[str, Any],
    key: str,
    action_id: int | None,
) -> list[str]:
    """Compatibility wrapper: a changed action is also a changed generation."""

    return reset_changed_generation_state(state, key, action_id, None)
''' + text[end:]

# Generation-aware duplicate decision.
start = text.index("    def apply_action_identity(\n")
end = text.index("    def assessment_availability(", start)
block = '''    def apply_action_identity(
        link: str,
        state: Any,
        result: Any,
        known_before: set[int],
    ):
        action_id = result.action_id
        if not isinstance(state, dict) or action_id is None:
            return result
        key = monitor_module.wheel_key(link)
        generation_status = action_generation_status(
            state, key, action_id, result.server_start_at
        )
        if action_id in known_before and generation_status == "same":
            if result.status == "inactive":
                return result
            return monitor_module.WheelAssessment(
                False,
                result.deadline,
                "это поколение колеса BetBoom уже было обработано",
                "duplicate_action",
                result.page_excerpt,
                action_id=action_id,
                available_at=result.available_at,
                verification_status=result.verification_status,
                server_start_at=result.server_start_at,
            )
        if generation_status in {"new_action", "new_generation"}:
            reset_changed_generation_state(
                state, key, action_id, result.server_start_at
            )
        return result

'''
text = text[:start] + block + text[end:]

# Preserve metadata in reconstructed assessments.
text = text.replace(
    "            verification_status=result.verification_status,\n        )\n",
    "            verification_status=result.verification_status,\n            server_start_at=result.server_start_at,\n        )\n",
)

# remember_active wrapper accepts and persists the server generation.
text = text.replace(
    '        verification_status="",\n    ):\n        original_remember_active(\n',
    '        verification_status="",\n        server_start_at=None,\n    ):\n        original_remember_active(\n',
    1,
)
text = text.replace(
    "            verification_status=verification_status,\n        )\n        _tag_availability(\n",
    "            verification_status=verification_status,\n            server_start_at=server_start_at,\n        )\n        _tag_availability(\n",
    1,
)
tag = '''        _tag_availability(
            monitor_module,
            original_deadline_parser,
            state,
            message,
            link,
            available_at=available_at,
            method=method,
        )
'''
if "record_generation_identity(\n            state," not in text:
    if tag not in text:
        raise RuntimeError("remember_active tag marker missing")
    text = text.replace(
        tag,
        tag + '''        record_generation_identity(
            state,
            monitor_module.wheel_key(link),
            action_id,
            server_start_at,
            current=monitor_module.now_utc(),
            status="active",
        )
''',
        1,
    )

# notify wrappers accept/pass the field. There are two wrappers.
text = text.replace(
    '        verification_status="",\n    ):\n        inferred_at, availability_method = _availability_for_message(\n',
    '        verification_status="",\n        server_start_at=None,\n    ):\n        inferred_at, availability_method = _availability_for_message(\n',
)
text = text.replace(
    "                verification_status=verification_status,\n            )\n",
    "                verification_status=verification_status,\n                server_start_at=server_start_at,\n            )\n",
)
text = text.replace(
    "            verification_status=verification_status,\n        )\n",
    "            verification_status=verification_status,\n            server_start_at=server_start_at,\n        )\n",
)
path.write_text(text, encoding="utf-8")
