from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import monitor


ROOT = Path(__file__).resolve().parent


def fake_page(text: str):
    class FakeResponse:
        status_code = 200

        def __init__(self, value: str) -> None:
            self.text = value

        def raise_for_status(self) -> None:
            return None

    return FakeResponse(text)


def main() -> None:
    assert monitor.normalize_url(
        "http://www.betboom.ru/freestream/Shoke/?x=1"
    ) == "https://betboom.ru/freestream/Shoke"
    assert monitor.wheel_key(
        "https://betboom.ru/freestream/Shoke"
    ) == monitor.wheel_key("https://www.betboom.ru/freestream/shoke/?from=tg")

    published = datetime(2026, 7, 13, 12, 0, tzinfo=monitor.UTC)
    deadline, _ = monitor.infer_deadline("Крутим через 1 час 20 минут", published)
    assert deadline == published + timedelta(hours=1, minutes=20)

    deadline = monitor.countdown_deadline("До прокрутки 00:15:30", published)
    assert deadline == published + timedelta(minutes=15, seconds=30)

    original_request = monitor.request_with_retries
    try:
        monitor.request_with_retries = lambda *args, **kwargs: fake_page(
            "<html><body>Пока ждёшь следующий запуск, заглядывай в другие акции</body></html>"
        )
        inspection = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/old-wheel"
        )
        assert inspection.status == "inactive"

        monitor.request_with_retries = lambda *args, **kwargs: fake_page(
            '<html><body><button aria-label="Участвовать">Участвовать</button></body></html>'
        )
        inspection = monitor.inspect_wheel_page(
            "https://betboom.ru/freestream/live-wheel"
        )
        assert inspection.status == "active"
        assert "кнопка" in inspection.method
    finally:
        monitor.request_with_retries = original_request

    message = monitor.Message(
        source="test",
        message_id=77,
        date=monitor.now_utc(),
        text="https://betboom.ru/freestream/pending-wheel",
        message_url="https://t.me/test/77",
    )
    link = "https://betboom.ru/freestream/pending-wheel"
    key = monitor.notification_key(message, link)
    state = {
        "pending_posts": {},
        "activation_alerts": {},
        "url_alerts": {},
    }
    monitor.remember_pending(
        state,
        key,
        message,
        link,
        "inactive",
        "not active yet",
        initial_notified=True,
    )
    assert key in state["pending_posts"]
    assert monitor.pending_initial_notified(state["pending_posts"][key])
    restored = monitor.pending_message(state["pending_posts"][key])
    assert restored is not None and restored.message_id == 77

    original_inspection = monitor.inspect_wheel_page
    monitor.inspect_wheel_page = lambda value: monitor.WheelInspection(
        "active", None, "активная кнопка: найдено «участвовать»"
    )
    try:
        should_notify, deadline, method, status = monitor.assess_pending_wheel(
            message, link
        )
        assert should_notify and status == "active"
        assert deadline is None and "кнопка" in method
    finally:
        monitor.inspect_wheel_page = original_inspection

    assert not monitor.is_activation_suppressed(state, link)
    monitor.remember_activation(state, link, None)
    assert monitor.is_activation_suppressed(state, link)

    quick = {item.casefold() for item in monitor.read_list(ROOT / "public_sources.txt")}
    nightly = {item.casefold() for item in monitor.read_list(ROOT / "source_catalog.txt")}
    assert not quick.intersection(nightly), "Быстрый и ночной списки пересекаются"
    assert "gazazor" in quick

    project_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "monitor.py",
            ROOT / "nightly_discovery.py",
            ROOT / ".github/workflows/monitor.yml",
        )
    )
    assert "known_freestream_ids" not in project_text
    assert "check_known_links" not in project_text

    print("Self-test passed.")


if __name__ == "__main__":
    main()
