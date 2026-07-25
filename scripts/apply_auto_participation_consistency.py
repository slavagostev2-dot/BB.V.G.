from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_function(path: Path, name: str, next_name: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = rf"^def {re.escape(name)}\(.*?(?=^def {re.escape(next_name)}\()"
    updated, count = re.subn(pattern, replacement.rstrip() + "\n\n\n", text, count=1, flags=re.M | re.S)
    if count != 1:
        raise RuntimeError(f"{path}: failed to replace {name}; matches={count}")
    path.write_text(updated, encoding="utf-8")


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: {label}; expected one match, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


bot_sync = ROOT / "auto_participation_bot_sync.py"
replace_once(
    bot_sync,
    'DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")\n',
    'DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")\n'
    'PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"\n'
    'PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"\n'
    'SUCCESS_STATUSES = {"participated", "already_participating", "already_marked_participating", "already_marked_in_bot"}\n',
    "insert primary identity",
)
replace_function(
    bot_sync,
    "_merge_timed_record",
    "_merge_record_collection",
    '''def _record_success(record: Any) -> bool:
    return isinstance(record, dict) and str(record.get("status") or "").casefold() in SUCCESS_STATUSES


def _merge_timed_record(remote: Any, local: Any) -> Any:
    if not isinstance(remote, dict):
        return copy.deepcopy(local)
    if not isinstance(local, dict):
        return copy.deepcopy(remote)

    remote_success = _record_success(remote)
    local_success = _record_success(local)
    if remote_success != local_success:
        winner = remote if remote_success else local
        loser = local if remote_success else remote
        result = copy.deepcopy(loser)
        result.update(copy.deepcopy(winner))
        for field in (
            "bot_failure_pending_at",
            "bot_failure_sync_status",
            "bot_failure_sync_version",
            "bot_failure_status",
            "bot_failure_detail",
        ):
            result.pop(field, None)
        result["status"] = str(winner.get("status") or "participated")
        result["retry_allowed"] = False
        return result

    local_is_newer = _record_timestamp(local) >= _record_timestamp(remote)
    older, newer = (remote, local) if local_is_newer else (local, remote)
    result = copy.deepcopy(older)
    result.update(copy.deepcopy(newer))
    return result''',
)
replace_once(
    bot_sync,
    '''        record = events.get(token)
        if not isinstance(record, dict):
            continue
        context = _event_context(state, attempt)
''',
    '''        record = events.get(token)
        if not isinstance(record, dict):
            continue
        record.setdefault("account_key", PRIMARY_ACCOUNT_KEY)
        record.setdefault("account_label", PRIMARY_ACCOUNT_LABEL)
        record.setdefault("event_token", token)
        context = _event_context(state, attempt)
''',
    "attach primary identity to queued outcomes",
)
replace_once(
    bot_sync,
    '''            for field in _AUTO_PARTICIPATION_FIELDS:
                if field in raw_item:
                    updated[field] = copy.deepcopy(raw_item[field])
            if bool(raw_item.get("participating")):
                updated["participating"] = True
            active[key] = updated
''',
    '''            success_already_confirmed = bool(
                current.get("participating")
                or current.get("auto_participation_confirmed_at")
                or str(current.get("auto_participation_status") or "").casefold() in SUCCESS_STATUSES
            )
            incoming_success = bool(
                raw_item.get("participating")
                or raw_item.get("auto_participation_confirmed_at")
                or str(raw_item.get("auto_participation_status") or "").casefold() in SUCCESS_STATUSES
            )
            for field in _AUTO_PARTICIPATION_FIELDS:
                if field not in raw_item:
                    continue
                if success_already_confirmed and not incoming_success and field in {
                    "participating",
                    "auto_participation_status",
                    "auto_participation_confirmed_at",
                    "auto_participation_retry_allowed",
                    "auto_participation_error",
                }:
                    continue
                updated[field] = copy.deepcopy(raw_item[field])
            if success_already_confirmed or incoming_success:
                updated["participating"] = True
                updated["auto_participation_status"] = "participated"
                updated["auto_participation_retry_allowed"] = False
                updated.pop("auto_participation_error", None)
            active[key] = updated
''',
    "make active success monotonic",
)

recovery = ROOT / "auto_participation_recovery.py"
replace_once(
    recovery,
    'ROOT = Path(__file__).resolve().parent\n',
    'ROOT = Path(__file__).resolve().parent\nPRIMARY_ACCOUNT_KEY = "vyacheslav_primary"\nPRIMARY_ACCOUNT_LABEL = "Аккаунт 1"\nSUCCESS_STATUSES = {"participated", "already_participating", "already_marked_participating", "already_marked_in_bot"}\n',
    "insert recovery identity",
)
replace_function(
    recovery,
    "_confirmed_success_for_event",
    "_ensure_button_context",
    '''def _record_matches_event(record: dict[str, Any], item: dict[str, Any]) -> bool:
    key = str(item.get("wheel_key") or "").casefold()
    if str(record.get("wheel_key") or "").casefold() != key:
        return False
    explicit = str(record.get("event_token") or "")
    if explicit:
        return explicit == _event_token(item)
    context = record.get("event_context")
    if isinstance(context, dict) and _event_token(context) == _event_token(item):
        return True
    started = monitor.parse_datetime(item.get("server_start_at") or item.get("message_date"))
    attempted = monitor.parse_datetime(
        record.get("bot_success_pending_at") or record.get("attempted_at") or record.get("recorded_at")
    )
    deadline = monitor.parse_datetime(item.get("deadline"))
    if started is None or attempted is None:
        return False
    if attempted < started - timedelta(minutes=5):
        return False
    if deadline is not None and attempted > deadline + timedelta(minutes=5):
        return False
    return True


def _confirmed_success_for_event(
    state: dict[str, Any],
    item: dict[str, Any],
) -> bool:
    """Return True when this exact event has any durable successful outcome."""

    key = str(item.get("wheel_key") or "").casefold()
    if not key:
        return False
    token = _event_token(item)

    processed = state.get("auto_participation_events")
    if isinstance(processed, dict):
        exact = processed.get(token)
        if isinstance(exact, dict) and str(exact.get("status") or "").casefold() in SUCCESS_STATUSES:
            return True
        for record in processed.values():
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or "").casefold() not in SUCCESS_STATUSES:
                continue
            if _record_matches_event(record, item):
                return True

    active = state.get("active_wheels")
    entry = active.get(key) if isinstance(active, dict) else None
    if isinstance(entry, dict) and _event_token(entry) == token:
        if bool(entry.get("participating")):
            return True
        if str(entry.get("auto_participation_status") or "").casefold() in SUCCESS_STATUSES:
            return True
        if entry.get("auto_participation_confirmed_at"):
            return True

    return False''',
)
replace_once(
    recovery,
    '''    record: dict[str, Any] = {
        "wheel_key": key,
        "status": status,
''',
    '''    record: dict[str, Any] = {
        "wheel_key": key,
        "account_key": PRIMARY_ACCOUNT_KEY,
        "account_label": PRIMARY_ACCOUNT_LABEL,
        "status": status,
''',
    "label primary recovery failure",
)
replace_once(
    recovery,
    '''        success_record: dict[str, Any] = {
            "wheel_key": key,
            "status": "participated",
''',
    '''        success_record: dict[str, Any] = {
            "wheel_key": key,
            "account_key": PRIMARY_ACCOUNT_KEY,
            "account_label": PRIMARY_ACCOUNT_LABEL,
            "event_token": token,
            "event_context": {field: item.get(field) for field in ("wheel_key", "action_id", "server_start_at", "message_date", "deadline") if item.get(field) is not None},
            "status": "participated",
''',
    "label primary recovery success",
)

notifications = ROOT / "auto_participation_notifications.py"
replace_function(
    notifications,
    "_base_event_token",
    "_account_identity",
    '''def _base_event_token(token: str, record: dict[str, Any]) -> str:
    explicit = str(record.get("event_token") or "").strip()
    if explicit:
        return explicit.split("#account:", 1)[0]
    return str(token or "").split("#account:", 1)[0]


def _canonical_event_token(
    state: dict[str, Any],
    token: str,
    record: dict[str, Any],
) -> str:
    base = _base_event_token(token, record)
    if "#action:" in base:
        return base
    context = record.get("event_context")
    if isinstance(context, dict):
        contextual = auto_participation_owner_sync._event_token(context)
        if "#action:" in contextual:
            return contextual
    key = str(record.get("wheel_key") or "").casefold()
    active = state.get("active_wheels")
    item = active.get(key) if isinstance(active, dict) else None
    if isinstance(item, dict):
        active_token = auto_participation_owner_sync._event_token(item, key)
        if "#action:" in active_token:
            return active_token
    return base''',
)
replace_function(
    notifications,
    "_account_identity",
    "_parse_datetime",
    '''def _account_identity(record: dict[str, Any]) -> tuple[str, str]:
    key = str(record.get("account_key") or "").strip()
    if not key:
        return "", ""
    if key == SECONDARY_ACCOUNT_KEY:
        return key, str(record.get("account_label") or SECONDARY_ACCOUNT_LABEL)
    if key == PRIMARY_ACCOUNT_KEY:
        return key, str(record.get("account_label") or PRIMARY_ACCOUNT_LABEL)
    return key, str(record.get("account_label") or key)''',
)
replace_function(
    notifications,
    "_settled_event_groups",
    "_notification_enabled",
    '''def _prefer_outcome(
    existing: tuple[str, dict[str, Any], bool] | None,
    incoming: tuple[str, dict[str, Any], bool],
) -> tuple[str, dict[str, Any], bool]:
    if existing is None:
        return incoming
    if existing[2] != incoming[2]:
        return incoming if incoming[2] else existing
    existing_at = max(
        (_parse_datetime(existing[1].get(field)) for field in ("bot_success_pending_at", "bot_failure_pending_at", "attempted_at", "recorded_at")),
        default=None,
        key=lambda value: value or datetime.min.replace(tzinfo=UTC),
    )
    incoming_at = max(
        (_parse_datetime(incoming[1].get(field)) for field in ("bot_success_pending_at", "bot_failure_pending_at", "attempted_at", "recorded_at")),
        default=None,
        key=lambda value: value or datetime.min.replace(tzinfo=UTC),
    )
    return incoming if (incoming_at or datetime.min.replace(tzinfo=UTC)) >= (existing_at or datetime.min.replace(tzinfo=UTC)) else existing


def _settled_event_groups(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, tuple[str, dict[str, Any], bool]]]:
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return {}
    approved_failures = {
        token: record
        for token, record in auto_participation_owner_sync.pending_failure_events(
            state, now=now
        )
    }
    groups: dict[str, dict[str, tuple[str, dict[str, Any], bool]]] = {}
    for raw_token, raw_record in events.items():
        if not isinstance(raw_record, dict):
            continue
        token = str(raw_token)
        account_key, _label = _account_identity(raw_record)
        if account_key not in {PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY}:
            continue
        is_success = _success(raw_record)
        if not is_success and token not in approved_failures:
            continue
        base_token = _canonical_event_token(state, token, raw_record)
        if not base_token:
            continue
        incoming = (token, raw_record, is_success)
        current = groups.setdefault(base_token, {}).get(account_key)
        groups[base_token][account_key] = _prefer_outcome(current, incoming)
    return {
        token: accounts
        for token, accounts in groups.items()
        if {PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY}.issubset(accounts)
    }''',
)
replace_function(
    notifications,
    "_result_message",
    "sync_once",
    '''def _success_description(record: dict[str, Any]) -> str:
    detail = str(record.get("detail") or "").casefold()
    if "post_click_layout" in detail or "об акции" in detail:
        return "участие подтверждено изменением страницы BetBoom"
    if str(record.get("status") or "").casefold() in {"already_participating", "already_marked_participating"}:
        return "участие уже было принято ранее"
    return "участие подтверждено BetBoom"


def _result_message(
    key: str,
    item: dict[str, Any],
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    all_success = all(value[2] for value in accounts.values())
    lines: list[str] = []
    for account_key in (PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY):
        _token, record, success = accounts[account_key]
        _key, label = _account_identity(record)
        escaped_label = html.escape(label or (PRIMARY_ACCOUNT_LABEL if account_key == PRIMARY_ACCOUNT_KEY else SECONDARY_ACCOUNT_LABEL))
        if success:
            lines.append(f"✅ {escaped_label} — {html.escape(_success_description(record))}")
        else:
            lines.append(f"❌ {escaped_label} — {html.escape(_failure_reason(record))}")
    title = "✅ <b>Участие принято</b>" if all_success else (
        "⚠️ <b>Автоучастие выполнено не полностью</b>"
        if any(value[2] for value in accounts.values())
        else "⚠️ <b>Участие не принято</b>"
    )
    return (
        f"{title}\n\n"
        f"Колесо: <code>{identifier}</code>\n"
        + "\n".join(lines),
        _navigation(),
    )''',
)

# Extend existing self-tests instead of creating another runtime module.
replace_once(
    notifications,
    '    print("auto participation notifications self-test passed")\n',
    '''    zonertg16_state = {
        "active_wheels": {
            "zonertg16": {
                "wheel_key": "zonertg16",
                "action_id": 701,
                "server_start_at": "2026-07-25T08:36:46.419000+00:00",
            }
        },
        "auto_participation_events": {
            "zonertg16#event:legacy": {
                "wheel_key": "zonertg16",
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "status": "participated",
                "attempted_at": "2026-07-25T09:05:17+00:00",
                "bot_success_pending_at": "2026-07-25T09:05:35+00:00",
            },
            "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00": {
                "wheel_key": "zonertg16",
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "status": "button_not_found",
                "attempted_at": "2026-07-25T09:06:21+00:00",
                "bot_failure_pending_at": "2026-07-25T09:05:36+00:00",
            },
            "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00#account:vyacheslav_secondary": {
                "wheel_key": "zonertg16",
                "account_key": SECONDARY_ACCOUNT_KEY,
                "account_label": SECONDARY_ACCOUNT_LABEL,
                "event_token": "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00",
                "status": "participated",
                "detail": "post_click_layout:main:Об акции",
                "attempted_at": "2026-07-25T09:05:50+00:00",
                "bot_success_pending_at": "2026-07-25T09:05:50+00:00",
            },
        },
    }
    grouped = _settled_event_groups(
        zonertg16_state,
        now=datetime(2026, 7, 25, 9, 10, tzinfo=UTC),
    )
    event = grouped["zonertg16#action:701:2026-07-25T08:36:46.419000+00:00"]
    assert event[PRIMARY_ACCOUNT_KEY][2] is True
    assert event[SECONDARY_ACCOUNT_KEY][2] is True
    text, _markup = _result_message("zonertg16", {"identifier": "zonertg16"}, event)
    assert "Аккаунт 1 — участие подтверждено BetBoom" in text
    assert "Аккаунт 2 — участие подтверждено изменением страницы BetBoom" in text
    assert "кнопка участия не найдена" not in text
    assert _account_identity({}) == ("", "")
    print("auto participation notifications self-test passed")
''',
    "add zonertg16 regression",
)

changelog = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
text = changelog.read_text(encoding="utf-8")
marker = "---\n"
entry = '''---

## 2026-07-25 — Исходы автоучастия сделаны монотонными и событийно-едиными

Подтверждённый успех BetBoom больше не может быть перезаписан более поздним
`button_not_found` из повторного recovery-прохода. Основной аккаунт получает
явные `account_key`/`account_label`, legacy-токены `#event` и `#action`
объединяются по точной идентичности `wheel_key + action_id + server_start_at`,
а агрегатор выбирает успешный исход независимо от порядка записей.

Итог Telegram теперь всегда содержит непустую строку по каждому аккаунту и
различает точное текстовое подтверждение от подтверждения по изменению страницы
BetBoom (`Принять участие` исчезла, осталась `Об акции`). Добавлен regression
сценария `zonertg16`: ранний success основного аккаунта, поздний ложный
`button_not_found` и успешный второй аккаунт обязаны дать общий успешный итог.

**Backup перед изменением:**
`backup/before-auto-participation-consistency-fix-20260725`.
'''
if entry not in text:
    text = text.replace(marker, entry, 1)
    changelog.write_text(text, encoding="utf-8")

print("auto participation consistency patch applied")
