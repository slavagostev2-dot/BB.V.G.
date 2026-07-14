from __future__ import annotations

import html
from typing import Any


def wheel_candidate_rows(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    for key, raw in candidates.items():
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or key).strip().lstrip("@")
        if not source or raw.get("admin_alerted_at"):
            continue
        if not raw.get("public"):
            continue
        if int(raw.get("wheel_links_found", 0) or 0) <= 0:
            continue
        rows.append((source, raw))
    rows.sort(
        key=lambda item: (
            -int(item[1].get("score", 0) or 0),
            -int(item[1].get("wheel_links_found", 0) or 0),
            item[0].casefold(),
        )
    )
    return rows


def candidate_message(source: str, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    found = int(entry.get("wheel_links_found", 0) or 0)
    score = int(entry.get("score", 0) or 0)
    latest = str(entry.get("latest_wheel_at") or "не определено")
    samples = entry.get("sample_wheels") if isinstance(entry.get("sample_wheels"), list) else []
    sample = next((row for row in samples if isinstance(row, dict)), {})
    identifier = str(sample.get("identifier") or "")
    message_url = str(sample.get("message_url") or "")

    lines = [
        "🛰️ <b>Разведка нашла новый источник с колёсами</b>",
        "",
        f"Канал: <b>@{html.escape(source)}</b>",
        f"Найдено ссылок на колёса: <b>{found}</b>",
        f"Оценка кандидата: <b>{score}/100</b>",
        f"Последнее колесо: <code>{html.escape(latest)}</code>",
    ]
    if identifier:
        lines.append(f"Пример: <code>{html.escape(identifier)}</code>")
    lines.extend(
        [
            "",
            "Добавить канал в основную проверку или пока оставить его в списке кандидатов?",
        ]
    )

    buttons: list[list[dict[str, str]]] = [
        [{"text": "📨 Открыть канал", "url": f"https://telegram.me/{source}"}],
        [
            {
                "text": "➕ Добавить в источники",
                "callback_data": f"candidate:mode:fast:{source}",
            },
            {
                "text": "⏸ Пока проигнорировать",
                "callback_data": f"candidate:defer:{source}",
            },
        ],
    ]
    if message_url:
        buttons.insert(1, [{"text": "🎡 Открыть найденный пост", "url": message_url}])
    return "\n".join(lines), {"inline_keyboard": buttons}


def notify_new_candidates(module: Any, state: dict[str, Any]) -> int:
    sent = 0
    for source, entry in wheel_candidate_rows(state):
        text, markup = candidate_message(source, entry)
        response = module.monitor.send_message(text, reply_markup=markup)
        result = response.get("result") if isinstance(response, dict) else None
        delivered = int(result.get("sent", 0) or 0) if isinstance(result, dict) else 0
        if delivered <= 0:
            continue
        entry["admin_alerted_at"] = module.now_iso()
        entry["admin_alert_delivery_count"] = delivered
        sent += 1
    return sent


def run(module: Any) -> int:
    result = module.main()
    if result != 0:
        return result
    state = module.load_state()
    sent = notify_new_candidates(module, state)
    if sent:
        module.save_state(state)
    print(f"Source intelligence administrator alerts sent: {sent}")
    return 0


def self_test() -> None:
    state = {
        "candidates": {
            "newsource": {
                "source": "NewSource",
                "public": True,
                "wheel_links_found": 2,
                "score": 75,
                "sample_wheels": [
                    {
                        "identifier": "wheel-a",
                        "message_url": "https://telegram.me/NewSource/10",
                    }
                ],
            },
            "empty": {"source": "Empty", "public": True, "wheel_links_found": 0},
            "sent": {
                "source": "Sent",
                "public": True,
                "wheel_links_found": 1,
                "admin_alerted_at": "now",
            },
        }
    }
    rows = wheel_candidate_rows(state)
    assert [source for source, _ in rows] == ["NewSource"]
    text, markup = candidate_message(*rows[0])
    assert "Разведка нашла новый источник" in text
    callbacks = [
        button.get("callback_data")
        for row in markup["inline_keyboard"]
        for button in row
        if button.get("callback_data")
    ]
    assert callbacks == [
        "candidate:mode:fast:NewSource",
        "candidate:defer:NewSource",
    ]
    print("source intelligence administrator alerts self-test passed")


if __name__ == "__main__":
    self_test()
