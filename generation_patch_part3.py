from pathlib import Path


path = Path("bbvg_monitor_runtime.py")
text = path.read_text(encoding="utf-8")

# Reconstructed assessments keep server_start_at.
text = text.replace(
    "            verification_status=result.verification_status,\n        )\n",
    "            verification_status=result.verification_status,\n            server_start_at=result.server_start_at,\n        )\n",
)

# An inactive tombstone blocks only the same generation.
start = text.index("def _inactive_blocks_result(")
end = text.index("\n\ndef _untimed_expiry", start)
text = text[:start] + '''def _inactive_blocks_result(state: dict, key: str, result: Any) -> bool:
    inactive = _inactive_entry(state, key)
    if inactive is None:
        return False
    current_action = _record_action_id({"action_id": result.action_id})
    if current_action is None:
        return True
    import wheel_event_runtime

    return wheel_event_runtime.action_generation_status(
        state, key, current_action, result.server_start_at
    ) == "same"
''' + text[end:]

# Revalidation rotates mutable state for a new action OR a later server start.
start = text.index("def _start_revalidated_action(")
end = text.index("\n\ndef revalidate_active_wheels", start)
text = text[:start] + '''def _start_revalidated_action(
    state: dict,
    active: dict,
    key: str,
    entry: dict,
    action_id: int | None,
    server_start_at,
    current,
) -> dict:
    import wheel_event_runtime
    import wheel_lifecycle_v2

    status = wheel_event_runtime.action_generation_status(
        state, key, action_id, server_start_at
    )
    if status not in {"new_action", "new_generation"}:
        wheel_event_runtime.record_generation_identity(
            state, key, action_id, server_start_at, current=current, status="active"
        )
        return entry

    preserved = dict(entry)
    wheel_event_runtime.reset_changed_generation_state(
        state, key, action_id, server_start_at
    )
    for field in (
        "event_id",
        "generation_id",
        "server_start_at",
        "lifecycle_state",
        "lifecycle_changed_at",
        "participating",
        "participating_at",
        "known_reminder_sent_at",
        "final_reminder_sent_at",
        "last_unknown_reminder_at",
        "manual_time_waiting_since",
        "availability_notified_at",
        "last_reminder_error",
        "final_reminder_error",
        "button_token",
    ):
        preserved.pop(field, None)
    preserved["first_notified_at"] = current.isoformat()
    preserved["participating"] = False
    if action_id is not None:
        preserved["action_id"] = action_id
    if server_start_at is not None:
        preserved["server_start_at"] = server_start_at.astimezone(monitor.UTC).isoformat()
    active[key] = preserved
    wheel_event_runtime.record_generation_identity(
        state, key, action_id, server_start_at, current=current, status="active"
    )
    wheel_lifecycle_v2.stamp_lifecycle(str(key).casefold(), preserved, current)
    return preserved
''' + text[end:]

old_call = '''            inspection.action_id,
            current,
        )
'''
new_call = '''            inspection.action_id,
            inspection.server_start_at,
            current,
        )
'''
if new_call not in text:
    if old_call not in text:
        raise RuntimeError("revalidation call marker missing")
    text = text.replace(old_call, new_call, 1)

# Closing via API records the exact generation before mutable cleanup.
old_inactive = '''        if inspection.status == "inactive":
            if inspection.action_id is not None:
                state.setdefault("wheel_action_history", {})[
                    str(key).casefold()
                ] = {
                    "action_id": inspection.action_id,
                    "seen_at": current.isoformat(),
                }
            import wheel_lifecycle_v2

            wheel_lifecycle_v2.cleanup_event_records(
                state, str(key).casefold()
            )
'''
new_inactive = '''        if inspection.status == "inactive":
            import wheel_event_runtime
            import wheel_lifecycle_v2

            wheel_event_runtime.record_generation_identity(
                state,
                str(key).casefold(),
                inspection.action_id,
                inspection.server_start_at,
                current=current,
                status="closed",
            )
            wheel_lifecycle_v2.cleanup_event_records(
                state, str(key).casefold()
            )
'''
if new_inactive not in text:
    if old_inactive not in text:
        raise RuntimeError("inactive close marker missing")
    text = text.replace(old_inactive, new_inactive, 1)

old_history = '''        if inspection.action_id is not None:
            entry["action_id"] = inspection.action_id
            state.setdefault("wheel_action_history", {})[str(key).casefold()] = {
                "action_id": inspection.action_id,
                "seen_at": current.isoformat(),
            }
'''
new_history = '''        if inspection.action_id is not None:
            entry["action_id"] = inspection.action_id
            import wheel_event_runtime

            wheel_event_runtime.record_generation_identity(
                state,
                str(key).casefold(),
                inspection.action_id,
                inspection.server_start_at,
                current=current,
                status="active",
            )
'''
if new_history not in text:
    if old_history not in text:
        raise RuntimeError("active history marker missing")
    text = text.replace(old_history, new_history, 1)

path.write_text(text, encoding="utf-8")
