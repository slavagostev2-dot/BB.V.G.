from __future__ import annotations

import os
from typing import Any

import private_state


def _admin_recipients(config: dict[str, Any]) -> list[str]:
    users = config.get("users") if isinstance(config.get("users"), dict) else {}
    admin_ids = {
        str(value)
        for value in [config.get("owner_id"), *config.get("admins", [])]
        if str(value or "")
    }
    result = {
        str((users.get(user_id) or {}).get("chat_id") or user_id)
        for user_id in admin_ids
        if isinstance(users.get(user_id), dict)
    }
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if not result and fallback:
        result.add(fallback)
    return sorted(value for value in result if value)


try:
    import notification_router

    def _private_notification_config() -> tuple[dict[str, Any], bool]:
        return private_state.load_access({})

    notification_router.load_config = _private_notification_config
except Exception as exc:  # pragma: no cover - startup must remain resilient
    print(f"WARNING private notification routing: {type(exc).__name__}: {exc}")


try:
    import source_tier_maintenance

    def _private_source_tier_recipients() -> list[str]:
        config, _ = private_state.load_access({})
        return _admin_recipients(config)

    source_tier_maintenance.notification_recipients = _private_source_tier_recipients
except Exception as exc:  # pragma: no cover
    print(f"WARNING private source tier routing: {type(exc).__name__}: {exc}")
