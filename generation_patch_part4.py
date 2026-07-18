from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"marker missing in {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Lifecycle IDs and terminal archives prefer the server generation.
replace_once(
    "wheel_lifecycle_v2.py",
    '    existing = str(record.get("event_id") or "").strip().casefold()\n',
    '    generation = str(record.get("generation_id") or "").strip().casefold()\n    if generation:\n        return generation[:64]\n    existing = str(record.get("event_id") or "").strip().casefold()\n',
)
replace_once(
    "wheel_lifecycle_v2.py",
    '        "changed_at": current.isoformat(),\n    }\n',
    '        "changed_at": current.isoformat(),\n        **({"action_id": int(entry["action_id"])} if str(entry.get("action_id") or "").isdigit() else {}),\n        **({"server_start_at": str(entry.get("server_start_at"))} if entry.get("server_start_at") else {}),\n        **({"generation_id": str(entry.get("generation_id"))} if entry.get("generation_id") else {}),\n    }\n',
)
helper = '''\n\ndef _close_generation_history(\n    state: dict[str, Any],\n    key: str,\n    entry: dict[str, Any],\n    current: datetime,\n    state_name: str,\n) -> None:\n    if not str(entry.get("action_id") or "").isdigit():\n        return\n    record = {\n        "action_id": int(entry["action_id"]),\n        "seen_at": current.isoformat(),\n        "closed_at": current.isoformat(),\n        "state": state_name,\n    }\n    if entry.get("server_start_at"):\n        record["server_start_at"] = str(entry["server_start_at"])\n    if entry.get("generation_id"):\n        record["generation_id"] = str(entry["generation_id"])\n    state.setdefault("wheel_action_history", {})[str(key).casefold()] = record\n'''
replace_once(
    "wheel_lifecycle_v2.py",
    "\n\ndef complete_event(\n",
    helper + "\n\ndef complete_event(\n",
)
replace_once(
    "wheel_lifecycle_v2.py",
    '    _remember_history(state, normalized, entry, "finished", reason, current)\n    return cleanup_event_records(state, normalized)\n',
    '    _remember_history(state, normalized, entry, "finished", reason, current)\n    _close_generation_history(state, normalized, entry, current, "closed")\n    return cleanup_event_records(state, normalized)\n',
)
replace_once(
    "wheel_lifecycle_v2.py",
    '    removed = cleanup_event_records(state, normalized)\n    state.setdefault("inactive_wheels", {})[normalized] = {\n',
    '    removed = cleanup_event_records(state, normalized)\n    _close_generation_history(state, normalized, record, current, "inactive")\n    state.setdefault("inactive_wheels", {})[normalized] = {\n',
)
replace_once(
    "wheel_lifecycle_v2.py",
    '        "lifecycle_state": "inactive",\n',
    '        **({"server_start_at": str(record.get("server_start_at"))} if record.get("server_start_at") else {}),\n        **({"generation_id": str(record.get("generation_id"))} if record.get("generation_id") else {}),\n        "lifecycle_state": "inactive",\n',
)
replace_once(
    "wheel_lifecycle_v2.py",
    '            {"action_id": int(entry["action_id"])}\n            if str(entry.get("action_id") or "").isdigit()\n            else {}\n        ),\n    }\n',
    '            {"action_id": int(entry["action_id"])}\n            if str(entry.get("action_id") or "").isdigit()\n            else {}\n        ),\n        **({"server_start_at": str(entry.get("server_start_at"))} if entry.get("server_start_at") else {}),\n        **({"generation_id": str(entry.get("generation_id"))} if entry.get("generation_id") else {}),\n    }\n',
)

# Personal participation/rating is generation-scoped, with action_id fallback.
replace_once(
    "personal_wheel_voting.py",
    '    action_id = str(record.get("action_id") or "").strip()\n',
    '    generation_id = str(record.get("generation_id") or "").strip().casefold()\n    if generation_id:\n        return f"{normalized}#generation:{generation_id[:64]}"\n    action_id = str(record.get("action_id") or "").strip()\n',
)
replace_once(
    "personal_wheel_voting.py",
    '            "event_id": item.get("event_id"),\n            "joined_at": datetime.now(UTC).isoformat(),\n',
    '            "event_id": item.get("event_id"),\n            "generation_id": item.get("generation_id"),\n            "server_start_at": item.get("server_start_at"),\n            "joined_at": datetime.now(UTC).isoformat(),\n',
)

# Update documented contracts.
replace_once(
    "AGENTS.md",
    '- одинаковый `action_id` BetBoom повторно не отправляется, даже если ссылку опубликовали снова после старого двухчасового окна;\n- новый `action_id` на прежней ссылке немедленно начинает чистое событие и не ждёт окончания таймера старой акции;\n',
    '- идентичность API-события определяется поколением сервера: при наличии `action_id` и `start_dttm` используется их совместная идентичность; одинаковые `action_id` + `start_dttm` повторно не отправляются;\n- подтверждённый более поздний `start_dttm` открывает чистое новое поколение даже при прежнем `action_id`; участие, публикации, suppression и рейтинг старого поколения не наследуются;\n- новый `action_id` на прежней ссылке также немедленно начинает чистое событие и не ждёт окончания таймера старой акции;\n',
)
replace_once(
    "AGENTS.md",
    '- личное участие хранится по событию: при наличии `action_id` ключ включает `action_id`, поэтому отметка старой акции не переносится на новую;\n',
    '- личное участие хранится по поколению события: при наличии серверного `generation_id` ключ включает поколение, а без него совместимо откатывается к `action_id`; отметка старого запуска не переносится на новый;\n',
)

changelog = Path("docs/PROJECT_CHANGELOG_RU.md")
text = changelog.read_text(encoding="utf-8")
heading = "## 2026-07-18 — Повторные серверные старты разделены на поколения\n"
if heading not in text:
    entry = '''## 2026-07-18 — Повторные серверные старты разделены на поколения\n\n**Причина:** BetBoom может повторно запустить прежнюю freestream-ссылку с тем же `action_id`. Старый контракт считал один `action_id` вечной идентичностью и подавлял такой запуск как дубль, поэтому новое колесо могло не получить отдельное участие и рейтинг.\n\n**Что изменено:**\n\n- авторитетная идентичность поколения строится по паре `action_id` + `start_dttm`;\n- одинаковые `action_id` и серверное время старта остаются дублем, но более поздний подтверждённый `start_dttm` открывает чистое новое поколение даже при том же `action_id`;\n- при смене поколения очищается только изменяемое состояние старого события: участие, публикации, suppression, таймерные overrides и кнопочные контексты;\n- завершение по API, дедлайну или административному совместимому пути помечает текущее поколение закрытым и сохраняет `server_start_at`/`generation_id` в истории;\n- личное участие и рейтинг предпочитают `generation_id`, поэтому один пользователь может отдельно участвовать и голосовать в двух серверных запусках с одинаковым `action_id`;\n- если BetBoom не вернул `start_dttm`, сохраняется консервативная совместимость: прежний `action_id` остаётся дублем, а без API-идентичности действуют правила ссылки и таймера.\n\n**Изменённые файлы:** `monitor.py`, `monitor_entry.py`, `wheel_event_runtime.py`, `wheel_link_lifecycle.py`, `bbvg_monitor_runtime.py`, `wheel_lifecycle_v2.py`, `personal_wheel_voting.py`, `tests/test_recurring_event_hotfix.py`, `tests/test_personal_wheel_voting.py`, `AGENTS.md`, `docs/PROJECT_CHANGELOG_RU.md`. Новых постоянных файлов нет; формат состояния расширен только необязательными полями `server_start_at` и `generation_id`.\n\n**Pre-update backup:** `backup/before-wheel-generation-2026-07-18` → `250d878ed028c93f6afe48810d464d8b9c1c1b82`; ref проверен как точное совпадение baseline `main`.\n\n**Откат:** вернуть merge commit целиком либо перейти на pre-update backup; отдельная миграция состояния не требуется, старые записи без `generation_id` читаются совместимо.\n\n'''
    marker = "---\n\n"
    if marker not in text:
        raise RuntimeError("changelog top marker missing")
    changelog.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")
