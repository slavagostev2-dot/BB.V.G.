from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import monitor


ROOT = Path(__file__).resolve().parent


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

    deadline, _ = monitor.infer_deadline("Прокрутка сегодня в 18:30", published)
    assert deadline is not None
    local = deadline.astimezone(monitor.MOSCOW)
    assert (local.hour, local.minute) == (18, 30)

    deadline = monitor.countdown_deadline("До прокрутки 00:15:30", published)
    assert deadline == published + timedelta(minutes=15, seconds=30)

    deadline = monitor.deadline_from_json(
        {"wheel": {"remainingSeconds": 900}}, published
    )
    assert deadline == published + timedelta(minutes=15)

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
