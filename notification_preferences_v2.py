from __future__ import annotations

from typing import Any, Callable

WHEEL_FINAL_REMINDERS = "wheel_final_reminders"
WHEEL_DRAW_ALERTS = "wheel_draw_alerts"


def install(router_module: Any) -> None:
    if getattr(router_module, "_bbvg_notification_preferences_v2_installed", False):
        return
    original_kind: Callable[[str], str] = router_module.notification_kind
    original_preference: Callable[[dict, str, dict, str], bool] = router_module.preference_enabled

    router_module.USER_NOTIFICATION_KINDS.update(
        {WHEEL_FINAL_REMINDERS, WHEEL_DRAW_ALERTS}
    )

    def notification_kind_v2(text: str) -> str:
        lowered = router_module.html.unescape(str(text or "")).casefold()
        if "время прокрутки колеса наступило" in lowered:
            return WHEEL_DRAW_ALERTS
        if (
            "напоминание о колесе betboom" in lowered
            or "последний шанс войти в колесо betboom" in lowered
            or ("последний шанс" in lowered and "колес" in lowered)
        ):
            return WHEEL_FINAL_REMINDERS
        return original_kind(text)

    def preference_enabled_v2(
        config: dict[str, Any],
        user_id: str,
        record: dict[str, Any],
        kind: str,
    ) -> bool:
        if kind in {WHEEL_FINAL_REMINDERS, WHEEL_DRAW_ALERTS}:
            raw = record.get("notification_preferences")
            if isinstance(raw, dict) and kind in raw:
                return bool(raw[kind])
            return original_preference(config, user_id, record, "wheels")
        return original_preference(config, user_id, record, kind)

    router_module.notification_kind = notification_kind_v2
    router_module.preference_enabled = preference_enabled_v2
    router_module._bbvg_notification_preferences_v2_installed = True


def self_test() -> None:
    import notification_router

    install(notification_router)
    assert notification_router.notification_kind(
        "🎯 Время прокрутки колеса наступило"
    ) == WHEEL_DRAW_ALERTS
    assert notification_router.notification_kind(
        "🚨 Напоминание о колесе BetBoom: последний шанс"
    ) == WHEEL_FINAL_REMINDERS
    config = {
        "owner_id": "1",
        "admins": [],
        "notification_recipients": ["10", "20"],
        "settings": {"notifications": True},
        "users": {
            "1": {
                "chat_id": "10",
                "notifications_enabled": True,
                "notification_preferences": {WHEEL_DRAW_ALERTS: False},
            },
            "2": {"chat_id": "20", "notifications_enabled": True},
        },
    }
    assert notification_router.recipients(config, True, WHEEL_DRAW_ALERTS) == ["20"]
    assert notification_router.recipients(config, True, WHEEL_FINAL_REMINDERS) == ["10", "20"]
    print("notification preferences v2 self-test passed")


if __name__ == "__main__":
    self_test()
