from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^{re.escape(start)}.*?(?=^{re.escape(end)})",
        re.MULTILINE | re.DOTALL,
    )
    updated, count = pattern.subn(lambda _match: replacement.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"{path}: expected one block {start!r} -> {end!r}, got {count}")
    path.write_text(updated, encoding="utf-8")


monitor_path = ROOT / "monitor.py"
replace_between(
    monitor_path,
    "def notify_new_link(",
    "def notify_activation(",
    '''def notify_new_link(
    message: Message,
    link: str,
    deadline: datetime | None,
    method: str,
    mappings: list[dict],
    state: dict | None = None,
    page_excerpt: str = "",
    *,
    action_id: int | None = None,
    available_at: datetime | None = None,
    verification_status: str = "",
    server_start_at: datetime | None = None,
) -> None:
    identifier_raw = wheel_identifier(link)
    identifier = html.escape(identifier_raw)
    published = message.date.astimezone(DISPLAY_TZ)
    timing = (
        f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>"
        if deadline
        else "🔴 <b>Время прокрутки неизвестно</b>"
    )

    verification = (
        "🟡 <b>Проверка активности временно недоступна</b>\\n"
        if verification_status == WHEEL_VERIFICATION_FAILED
        else ""
    )
    referral_notice = wheel_publications_v2.referral_restriction_notice(message.text)
    referral_line = f"{referral_notice}\\n" if referral_notice else ""

    # The event and its dispatch outbox must exist before the external Telegram
    # delivery. A notification checkpoint advances main, so persisting afterwards
    # creates a race where the card exists but auto participation has no event.
    if state is not None:
        remember_active_wheel(
            state,
            message,
            link,
            deadline,
            "preliminary",
            method,
            page_excerpt,
            action_id=action_id,
            available_at=available_at,
            verification_status=verification_status,
            server_start_at=server_start_at,
        )
        dispatch_notified_wheel_event(state, link)

    send_message(
        "🎡 <b>Новое колесо BetBoom</b>\\n\\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\\n"
        f"Идентификатор: <code>{identifier}</code>\\n"
        f"Пост: {published:%d.%m.%Y %H:%M}\\n"
        f"{verification}"
        f"{referral_line}"
        f"{timing}",
        reply_markup=(
            wheel_reply_markup(
                state, message, link, active=False, status="preliminary",
                method=method, page_excerpt=page_excerpt
            ) if state is not None else None
        ),
        url=link if state is None else None,
    )
''',
)

replace_between(
    monitor_path,
    "def notify_activation(",
    "def fetch_all_sources(",
    '''def notify_activation(
    message: Message,
    link: str,
    deadline: datetime | None,
    method: str,
    mappings: list[dict],
    state: dict | None = None,
    page_excerpt: str = "",
    *,
    action_id: int | None = None,
    available_at: datetime | None = None,
    verification_status: str = "",
    server_start_at: datetime | None = None,
) -> None:
    identifier_raw = wheel_identifier(link)
    identifier = html.escape(identifier_raw)
    published = message.date.astimezone(DISPLAY_TZ)
    timing = (
        f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>"
        if deadline
        else "🔴 <b>Время прокрутки неизвестно</b>"
    )
    verification = (
        "🟡 <b>Проверка активности временно недоступна</b>\\n"
        if verification_status == WHEEL_VERIFICATION_FAILED
        else ""
    )
    referral_notice = wheel_publications_v2.referral_restriction_notice(message.text)
    referral_line = f"{referral_notice}\\n" if referral_notice else ""

    if state is not None:
        remember_active_wheel(
            state,
            message,
            link,
            deadline,
            "active",
            method,
            page_excerpt,
            action_id=action_id,
            available_at=available_at,
            verification_status=verification_status,
            server_start_at=server_start_at,
        )
        dispatch_notified_wheel_event(state, link)

    send_message(
        "✅ <b>Колесо BetBoom стало активно</b>\\n\\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\\n"
        f"Идентификатор: <code>{identifier}</code>\\n"
        f"Пост: {published:%d.%m.%Y %H:%M}\\n"
        f"{verification}"
        f"{referral_line}"
        f"{timing}",
        reply_markup=(
            wheel_reply_markup(
                state, message, link, active=True, status="active",
                method=method, page_excerpt=page_excerpt
            ) if state is not None else None
        ),
        url=link if state is None else None,
    )
''',
)

wheel_path = ROOT / "wheel_event_runtime.py"
replace_between(
    wheel_path,
    "def _availability_message(",
    "def process_due_availability(",
    '''def _availability_message(
    monitor_module: Any,
    state: dict[str, Any],
    message: Any,
    link: str,
    available_at: datetime,
    method: str,
    deadline: datetime | None = None,
    *,
    action_id: int | None = None,
    verification_status: str = "",
    server_start_at: datetime | None = None,
) -> None:
    current = monitor_module.now_utc()
    future = available_at > current
    identifier = html.escape(monitor_module.wheel_identifier(link))
    published = message.date.astimezone(monitor_module.DISPLAY_TZ)
    if future:
        title = "🟡 <b>Новое колесо BetBoom — участие откроется позже</b>"
        timing = (
            "🕒 Будет доступно через: "
            f"<b>{html.escape(monitor_module.human_remaining(available_at))}</b>"
        )
        if deadline is not None:
            timing += (
                "\\n⏳ До прокрутки: "
                f"<b>{html.escape(monitor_module.human_remaining(deadline))}</b>"
            )
        status = "scheduled_availability"
    else:
        title = "🟢 <b>Колесо BetBoom доступно для участия</b>"
        timing = (
            "✅ Можно участвовать сейчас\\n"
            + (
                "⏳ До прокрутки: "
                f"<b>{html.escape(monitor_module.human_remaining(deadline))}</b>"
                if deadline is not None
                else "🔴 <b>Время прокрутки неизвестно</b>"
            )
        )
        status = "available"
    verification = (
        "\\n🟡 <b>Проверка активности временно недоступна</b>"
        if verification_status == monitor_module.WHEEL_VERIFICATION_FAILED
        else ""
    )

    monitor_module.remember_active_wheel(
        state,
        message,
        link,
        deadline,
        status,
        method,
        "",
        action_id=action_id,
        available_at=available_at,
        verification_status=verification_status,
        server_start_at=server_start_at,
    )
    _tag_availability(
        monitor_module,
        monitor_module._bbvg_original_deadline_parser,
        state,
        message,
        link,
        available_at=available_at,
        method=method,
    )
    monitor_module.dispatch_notified_wheel_event(state, link)

    monitor_module.send_message(
        f"{title}\\n\\n"
        f"Источник: <a href=\"{html.escape(message.message_url, quote=True)}\">"
        f"@{html.escape(message.source)}</a>\\n"
        f"Идентификатор: <code>{identifier}</code>\\n"
        f"Пост: {published:%d.%m.%Y %H:%M}\\n"
        f"{timing}{verification}",
        reply_markup=monitor_module.wheel_reply_markup(
            state,
            message,
            link,
            active=not future,
            status=status,
            method=method,
        ),
    )
''',
)

dispatch_path = ROOT / "auto_participation_dispatch.py"
dispatch = dispatch_path.read_text(encoding="utf-8")
if "import base64\n" not in dispatch:
    dispatch = dispatch.replace("from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport base64\n", 1)
if "import auto_participation_bot_sync\n" not in dispatch:
    dispatch = dispatch.replace("import requests\n", "import requests\n\nimport auto_participation_bot_sync\n", 1)
pattern = re.compile(
    r"^def _rebase_before_retry\(\).*?(?=^def _dispatch\()",
    re.MULTILINE | re.DOTALL,
)
replacement = '''def _state_endpoint() -> str:
    return f"https://api.github.com/repos/{_repository()}/contents/state.json"


def _remote_state() -> tuple[dict[str, Any], str]:
    response = requests.get(
        _state_endpoint(),
        headers=_headers(),
        params={"ref": _branch()},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    raw = base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
    value = json.loads(raw)
    return (value if isinstance(value, dict) else {}), str(payload.get("sha") or "")


def _put_remote_state(value: dict[str, Any], sha: str) -> requests.Response:
    body: dict[str, Any] = {
        "message": "Persist auto participation dispatch state [skip ci]",
        "content": base64.b64encode(
            (json.dumps(value, ensure_ascii=False, indent=2) + "\\n").encode("utf-8")
        ).decode("ascii"),
        "branch": _branch(),
    }
    if sha:
        body["sha"] = sha
    return requests.put(
        _state_endpoint(),
        headers=_headers(),
        json=body,
        timeout=_TIMEOUT,
    )


def _push_state_before_dispatch(state: dict[str, Any]) -> tuple[bool, str]:
    """Publish the event with file-level CAS instead of rebasing the live monitor.

    Notification claims and checkpoints intentionally create commits in ``main``.
    A local ``git pull --rebase`` therefore races the delivery path and can lose the
    newly detected active event. The Contents API retries only when ``state.json``
    itself changed and merges the event/dispatch ledgers with the latest remote
    monitor snapshot.
    """

    local = state if isinstance(state, dict) else {}
    last_error = ""
    for attempt in range(1, 6):
        try:
            remote, sha = _remote_state()
            merged = auto_participation_bot_sync.merge_auto_participation_state(
                remote, local
            )
            response = _put_remote_state(merged, sha)
            if response.status_code in {409, 422}:
                last_error = f"state_cas_conflict:http_{response.status_code}"
                time.sleep(0.35 * attempt)
                continue
            response.raise_for_status()
            _write_state(merged)
            state.clear()
            state.update(merged)
            return True, ""
        except Exception as exc:
            last_error = f"state_cas_failed:{type(exc).__name__}: {exc}"[:500]
            if attempt < 5:
                time.sleep(0.35 * attempt)
                continue
    return False, last_error or "state_cas_failed:unknown"

'''
dispatch, count = pattern.subn(lambda _match: replacement, dispatch, count=1)
if count != 1:
    raise RuntimeError(f"auto_participation_dispatch.py: dispatch persistence block count={count}")
dispatch_path.write_text(dispatch, encoding="utf-8")

tests_path = ROOT / "tests" / "test_current_contracts.py"
tests = tests_path.read_text(encoding="utf-8")
marker = "def test_auto_participation_event_is_durable_before_notification_delivery()"
if marker not in tests:
    tests += '''\n\ndef _source_block(path: str, start: str, end: str) -> str:\n    source = Path(path).read_text(encoding="utf-8")\n    return source.split(start, 1)[1].split(end, 1)[0]\n\n\ndef test_auto_participation_event_is_durable_before_notification_delivery() -> None:\n    new_wheel = _source_block("monitor.py", "def notify_new_link(", "def notify_activation(")\n    activation = _source_block("monitor.py", "def notify_activation(", "def fetch_all_sources(")\n    availability = _source_block(\n        "wheel_event_runtime.py",\n        "def _availability_message(",\n        "def process_due_availability(",\n    )\n    for block in (new_wheel, activation, availability):\n        assert block.index("remember_active_wheel(") < block.index("send_message(")\n        assert block.index("dispatch_notified_wheel_event") < block.index("send_message(")\n\n\ndef test_auto_participation_dispatch_uses_state_file_cas() -> None:\n    source = Path("auto_participation_dispatch.py").read_text(encoding="utf-8")\n    block = source.split("def _push_state_before_dispatch", 1)[1].split("def _dispatch", 1)[0]\n    assert "merge_auto_participation_state" in block\n    assert "_put_remote_state" in block\n    assert "git pull" not in block\n    assert "git rebase" not in block\n'''
tests_path.write_text(tests, encoding="utf-8")

changelog_path = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
changelog = changelog_path.read_text(encoding="utf-8")
heading = "### Атомарная постановка колеса в автоучастие"
if heading not in changelog:
    changelog += '''\n\n### Атомарная постановка колеса в автоучастие\n\n- активное событие и dispatch-outbox теперь фиксируются до внешней Telegram-доставки;\n- notification claim/checkpoint больше не может оставить карточку без события автоучастия;\n- `auto_participation_dispatch.py` публикует объединённый `state.json` через file-level compare-and-swap Contents API вместо `git pull --rebase` в рабочем каталоге Monitor;\n- конфликт изменения `state.json` повторяется с объединением последнего monitor-state, а изменения других файлов в `main` не мешают dispatch;\n- добавлен regression-контракт для повторно используемого URL: карточка не может появиться раньше устойчивого event/outbox.\n'''
changelog_path.write_text(changelog, encoding="utf-8")

print("Atomic auto participation dispatch patch prepared")
