from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _ordered(text: str, markers: tuple[str, ...]) -> bool:
    positions = [text.index(marker) for marker in markers]
    return positions == sorted(positions)


def test_current_monitor_composition_order_is_documented_and_frozen() -> None:
    """Record the production install order without importing and mutating it."""

    source = _text("bbvg_monitor_main.py")
    assert _ordered(
        source,
        (
            "notification_preferences_v2.install(notification_router)",
            "recurring_wheel_events.install(monitor, runtime.base_runtime)",
            "telegram_transport.install(monitor)",
            "telegram_post_links_v2.install(monitor)",
            "wheel_event_runtime.install(monitor, runtime)",
            "wheel_publications_v2.install(monitor, runtime)",
            "restart_duplicate_guard.install(monitor)",
            "wheel_link_lifecycle.install(monitor)",
            "wheel_lifecycle_v2.install(monitor)",
            "personal_reminder_filter.install(monitor, notification_router)",
        ),
    )

    baseline = _text("engineering/WHEEL_PIPELINE_BASELINE_RU.md")
    for section in (
        "Текущий путь обнаружения",
        "Текущий путь первичного уведомления",
        "Текущий жизненный цикл",
        "Текущий путь автоучастия",
        "Текущий путь итогового сообщения",
        "Recovery-handoff",
        "Замороженные контракты этапа 1",
    ):
        assert section in baseline
