from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc
PAGE_SIZE = 15


def render(runtime: Any, page: int = 0) -> None:
    """Render the single production list of current BetBoom wheels.

    Collection/lifecycle truth stays in WheelInteractionRuntime. This module owns
    only the Telegram presentation of that data so voting, navigation and runtime
    compatibility layers no longer need competing show_active implementations.
    """

    snap = runtime.snapshot(force=True)
    items = runtime._collect_current_wheels()
    status = runtime._monitor_status()
    checked_at = status.get("last_successful_iteration_at")

    if not items:
        state_line = (
            f"Обновлено: {runtime.fmt_dt(checked_at)} ({runtime.age_text(checked_at)})"
            if checked_at
            else "Ожидаются данные проверки"
        )
        runtime.send(
            f"🎡 <b>Колёс сейчас нет.</b>\n\n{state_line}",
            reply_markup=runtime.with_nav(
                [[{"text": "🔄 Обновить", "callback_data": "refresh:active:0"}]]
            ),
        )
        return

    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    normalized_page = max(0, min(int(page), pages - 1))
    start = normalized_page * PAGE_SIZE
    visible = items[start : start + PAGE_SIZE]

    lines = [f"🎡 <b>Колёса — {len(items)}</b>"]
    if pages > 1:
        lines.append(f"Страница: <b>{normalized_page + 1} из {pages}</b>")
    lines.append("")

    buttons: list[list[dict[str, str]]] = []
    current = datetime.now(UTC)
    for offset, item in enumerate(visible):
        index = start + offset + 1
        identifier = str(item.get("identifier") or item.get("_key") or "колесо")
        key = str(item.get("_key") or identifier).strip().casefold()
        deadline = runtime.parse_dt(item.get("deadline"))
        available_at = runtime.parse_dt(item.get("available_at"))
        sources = runtime._sources_for_item(snap, key, item)
        source_text = ", ".join(f"@{source}" for source in sources) or "источник неизвестен"

        if available_at and available_at > current:
            state_text = "🟠 Ожидает запуска"
            timing = f"Участие откроется через {runtime.remaining(available_at)}"
        elif deadline:
            state_text = "🟢 Время прокрутки известно"
            timing = runtime.remaining(deadline)
        else:
            state_text = "🟡 Время уточняется"
            timing = "Бот продолжает проверять BetBoom"

        lines.extend(
            [
                f"<b>{index}. <code>{html.escape(identifier[:100])}</code></b>",
                state_text,
                f"⏳ {html.escape(timing)}",
                f"📡 {html.escape(source_text)}",
                "",
            ]
        )
        url = str(item.get("url") or "")
        if url:
            buttons.append([{"text": f"🎡 {index} · Открыть", "url": url}])

    pager: list[dict[str, str]] = []
    if normalized_page > 0:
        pager.append(
            {
                "text": "◀️ Назад",
                "callback_data": f"page:active:{normalized_page - 1}",
            }
        )
    if normalized_page < pages - 1:
        pager.append(
            {
                "text": "Вперёд ▶️",
                "callback_data": f"page:active:{normalized_page + 1}",
            }
        )
    if pager:
        buttons.append(pager)
    buttons.append(
        [
            {
                "text": "🔄 Обновить",
                "callback_data": f"refresh:active:{normalized_page}",
            }
        ]
    )
    runtime.send("\n".join(lines).rstrip(), reply_markup=runtime.with_nav(buttons))
