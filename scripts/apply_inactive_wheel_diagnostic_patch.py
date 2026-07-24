from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def patch_panel() -> None:
    path = ROOT / "admin_panel_v2.py"
    text = path.read_text(encoding="utf-8")
    old = '''        for entry in snap.state.get("active_wheels", {}).values():
            if isinstance(entry, dict) and str(entry.get("source") or "").casefold() == source.casefold() and int(entry.get("message_id") or 0) == node_id:
                state_hits.append("колесо находится в активном списке")
        reason = "; ".join(state_hits) if state_hits else (
            "пост ещё не попал в состояние монитора" if source.casefold() in primary | reserve else "канал не включён в мониторинг"
        )
'''
    new = '''        for entry in snap.state.get("active_wheels", {}).values():
            if isinstance(entry, dict) and str(entry.get("source") or "").casefold() == source.casefold() and int(entry.get("message_id") or 0) == node_id:
                state_hits.append("колесо находится в активном списке")

        if not state_hits:
            wheel_keys = {
                str(link).split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].casefold()
                for link in wheel_links
                if str(link).strip()
            }
            observations = [
                entry
                for entry in snap.state.get("wheel_generation_observations", {}).values()
                if isinstance(entry, dict)
                and str(entry.get("wheel_key") or "").casefold() in wheel_keys
            ]
            if observations:
                latest = max(
                    observations,
                    key=lambda entry: str(
                        entry.get("last_seen_at") or entry.get("first_seen_at") or ""
                    ),
                )
                statuses = latest.get("statuses")
                statuses = statuses if isinstance(statuses, dict) else {}
                action_id = str(latest.get("action_id") or "не указан")
                server_start = str(latest.get("server_start_at") or "не указан")
                if int(statuses.get("inactive", 0) or 0) > 0:
                    state_hits.append(
                        "колесо найдено монитором, но к моменту проверки BetBoom "
                        f"уже закрыл участие; action_id {action_id}; "
                        f"серверный старт {server_start}"
                    )
                elif int(statuses.get("active", 0) or 0) > 0:
                    state_hits.append(
                        "колесо было подтверждено BetBoom; "
                        f"action_id {action_id}; серверный старт {server_start}"
                    )
                else:
                    state_hits.append(
                        "колесо найдено в истории проверок BetBoom; "
                        f"action_id {action_id}; серверный старт {server_start}"
                    )

        reason = "; ".join(state_hits) if state_hits else (
            "пост ещё не попал в состояние монитора" if source.casefold() in primary | reserve else "канал не включён в мониторинг"
        )
'''
    path.write_text(replace_once(text, old, new, label="diagnostic history"), encoding="utf-8")


def add_test() -> None:
    path = ROOT / "tests" / "test_inactive_wheel_diagnostic.py"
    path.write_text(
        '''from types import SimpleNamespace\n\nimport admin_panel_v2\n\n\ndef test_diagnostic_shows_inactive_betboom_event(monkeypatch) -> None:\n    panel = object.__new__(admin_panel_v2.TelegramPanelV2)\n    panel.snapshot = lambda: SimpleNamespace(\n        fast=["kolesaBB"],\n        nightly=[],\n        state={\n            "pending_posts": {},\n            "active_wheels": {},\n            "wheel_generation_observations": {\n                "current": {\n                    "wheel_key": "aunkere",\n                    "action_id": 1021,\n                    "server_start_at": "2026-07-24T17:55:53.181000+00:00",\n                    "last_seen_at": "2026-07-24T18:33:21.656342+00:00",\n                    "statuses": {"inactive": 1},\n                }\n            },\n        },\n    )\n    panel.load_access = lambda: {\n        "settings": {"monitor_interval_minutes": 1}\n    }\n\n    response = SimpleNamespace(\n        status_code=200,\n        text=(\n            '<div class="tgme_widget_message" data-post="kolesaBB/245">'\n            '<div class="tgme_widget_message_text">Колесо BetBoom</div>'\n            '<a href="https://betboom.ru/freestream/aunkere">Открыть</a>'\n            '</div>'\n        ),\n    )\n    monkeypatch.setattr(admin_panel_v2.requests, "get", lambda *args, **kwargs: response)\n\n    result = panel.diagnose_input("https://t.me/kolesaBB/245")\n\n    assert "основная проверка каждую минуту" in result\n    assert "колесо найдено монитором" in result\n    assert "уже закрыл участие" in result\n    assert "action_id 1021" in result\n    assert "пост ещё не попал" not in result\n''',
        encoding="utf-8",
    )


def main() -> None:
    patch_panel()
    add_test()


if __name__ == "__main__":
    main()
