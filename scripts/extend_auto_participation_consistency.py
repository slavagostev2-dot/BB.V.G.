from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: {label}; expected one match, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_function(path: Path, name: str, next_name: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = rf"^def {re.escape(name)}\(.*?(?=^def {re.escape(next_name)}\()"
    updated, count = re.subn(
        pattern,
        replacement.rstrip() + "\n\n\n",
        text,
        count=1,
        flags=re.M | re.S,
    )
    if count != 1:
        raise RuntimeError(f"{path}: failed to replace {name}; matches={count}")
    path.write_text(updated, encoding="utf-8")


# Canonical action-first identity, explicit primary account and state migration.
auto = ROOT / "betboom_auto_participation.py"
replace_once(
    auto,
    '_PARTICIPATION_ATTEMPT_VERSION = 2\n',
    '''_PARTICIPATION_ATTEMPT_VERSION = 2
PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"
PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"
PRIMARY_ACCOUNT_OWNER = "vyacheslav"
PRIMARY_ACCOUNT_ORDER = 10
SECONDARY_ACCOUNT_KEY = "vyacheslav_secondary"
SECONDARY_ACCOUNT_LABEL = "Аккаунт 2"
SECONDARY_ACCOUNT_ORDER = 20
SUCCESS_STATUSES = {
    "participated",
    "already_participating",
    "already_marked_participating",
    "already_marked_in_bot",
}
''',
    "insert canonical account constants",
)
identity_helpers = '''def register_account(
    state: dict[str, Any],
    *,
    account_key: str,
    account_label: str,
    account_owner: str,
    account_order: int,
) -> bool:
    registry = state.setdefault("auto_participation_account_registry", {})
    record = {
        "account_key": str(account_key),
        "account_label": str(account_label or account_key),
        "account_owner": str(account_owner),
        "account_order": int(account_order),
        "enabled": True,
    }
    if registry.get(account_key) == record:
        return False
    registry[account_key] = record
    return True


def ensure_default_account_registry(state: dict[str, Any]) -> bool:
    changed = register_account(
        state,
        account_key=PRIMARY_ACCOUNT_KEY,
        account_label=PRIMARY_ACCOUNT_LABEL,
        account_owner=PRIMARY_ACCOUNT_OWNER,
        account_order=PRIMARY_ACCOUNT_ORDER,
    )
    changed = register_account(
        state,
        account_key=SECONDARY_ACCOUNT_KEY,
        account_label=SECONDARY_ACCOUNT_LABEL,
        account_owner=PRIMARY_ACCOUNT_OWNER,
        account_order=SECONDARY_ACCOUNT_ORDER,
    ) or changed
    return changed


def _event_token(key: str, entry: dict[str, Any]) -> str:
    """Use the BetBoom action identity before internal generation aliases."""

    normalized = str(key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
    try:
        action_id = int(entry.get("action_id") or 0)
    except (TypeError, ValueError):
        action_id = 0
    server_start = str(entry.get("server_start_at") or "").strip()
    if action_id > 0:
        return f"{normalized}#action:{action_id}:{server_start}"

    event_id = str(entry.get("event_id") or entry.get("generation_id") or "").strip()
    if event_id:
        return f"{normalized}#event:{event_id}"

    first_seen = str(
        entry.get("first_notified_at")
        or entry.get("message_date")
        or entry.get("created_at")
        or ""
    ).strip()
    return f"{normalized}#seen:{first_seen}"


def _record_account_key(token: str, record: dict[str, Any]) -> str:
    explicit = str(record.get("account_key") or "").strip()
    if explicit:
        return explicit
    if "#account:" in str(token):
        return str(token).split("#account:", 1)[1].strip()
    return PRIMARY_ACCOUNT_KEY


def _record_success(record: Any) -> bool:
    return isinstance(record, dict) and str(record.get("status") or "").casefold() in SUCCESS_STATUSES


def _record_time(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    return max(
        (
            str(record.get(field) or "")
            for field in (
                "bot_success_pending_at",
                "bot_failure_pending_at",
                "attempted_at",
                "recorded_at",
            )
        ),
        default="",
    )


def merge_event_record(previous: Any, incoming: Any) -> dict[str, Any]:
    left = dict(previous) if isinstance(previous, dict) else {}
    right = dict(incoming) if isinstance(incoming, dict) else {}
    left_success = _record_success(left)
    right_success = _record_success(right)
    if left_success != right_success:
        winner, older = (left, right) if left_success else (right, left)
    elif _record_time(right) >= _record_time(left):
        winner, older = right, left
    else:
        winner, older = left, right
    result = dict(older)
    result.update(winner)
    if _record_success(result):
        for field in (
            "bot_failure_pending_at",
            "bot_failure_sync_status",
            "bot_failure_sync_version",
            "bot_failure_status",
            "bot_failure_detail",
            "manual_notification_sent",
            "manual_notification_at",
            "manual_notification_detail",
        ):
            result.pop(field, None)
        result["retry_allowed"] = False
    return result


def _record_matches_active_event(
    token: str,
    record: dict[str, Any],
    key: str,
    entry: dict[str, Any],
) -> bool:
    canonical = _event_token(key, entry)
    base = str(token).split("#account:", 1)[0]
    explicit = str(record.get("event_token") or "").split("#account:", 1)[0]
    if canonical in {base, explicit}:
        return True
    legacy_id = str(entry.get("event_id") or entry.get("generation_id") or "").strip()
    if legacy_id and f"{key}#event:{legacy_id}" in {base, explicit}:
        return True
    context = record.get("event_context")
    if isinstance(context, dict) and _event_token(key, context) == canonical:
        return True
    return False


def canonicalize_primary_event_aliases(state: dict[str, Any]) -> bool:
    """Collapse legacy #event primary rows into the canonical #action row."""

    changed = ensure_default_account_registry(state)
    events = state.setdefault("auto_participation_events", {})
    active = state.get("active_wheels")
    if not isinstance(events, dict) or not isinstance(active, dict):
        return changed
    for raw_key, entry in list(active.items()):
        if not isinstance(entry, dict):
            continue
        key = str(raw_key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
        canonical = _event_token(key, entry)
        for raw_token in list(events):
            record = events.get(raw_token)
            if not isinstance(record, dict):
                continue
            if _record_account_key(str(raw_token), record) != PRIMARY_ACCOUNT_KEY:
                continue
            if not _record_matches_active_event(str(raw_token), record, key, entry):
                continue
            suffix = (
                f"#account:{PRIMARY_ACCOUNT_KEY}"
                if "#account:" in str(raw_token)
                else ""
            )
            target = canonical + suffix
            normalized = dict(record)
            normalized.update(
                {
                    "event_token": canonical,
                    "account_key": PRIMARY_ACCOUNT_KEY,
                    "account_label": PRIMARY_ACCOUNT_LABEL,
                    "account_owner": PRIMARY_ACCOUNT_OWNER,
                    "account_order": PRIMARY_ACCOUNT_ORDER,
                }
            )
            merged = merge_event_record(events.get(target), normalized)
            if events.get(target) != merged:
                events[target] = merged
                changed = True
            if str(raw_token) != target:
                events.pop(raw_token, None)
                changed = True
    return changed


'''
replace_function(auto, "_event_token", "_eligible_for_event_attempt", identity_helpers)
replace_once(
    auto,
    '''    active = state.setdefault("active_wheels", {})
    events = state.setdefault("auto_participation_events", {})
    changed = False
''',
    '''    active = state.setdefault("active_wheels", {})
    events = state.setdefault("auto_participation_events", {})
    changed = canonicalize_primary_event_aliases(state)
''',
    "canonicalize primary aliases before attempt",
)
replace_once(
    auto,
    '''            events[token] = {
                "wheel_key": str(key).casefold(),
                "status": "baseline_existing",
                "recorded_at": current.isoformat(),
            }
''',
    '''            events[token] = {
                "wheel_key": str(key).casefold(),
                "event_token": token,
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "account_owner": PRIMARY_ACCOUNT_OWNER,
                "account_order": PRIMARY_ACCOUNT_ORDER,
                "status": "baseline_existing",
                "recorded_at": current.isoformat(),
            }
''',
    "label primary baseline",
)
replace_once(
    auto,
    '''            events[token] = {
                "wheel_key": normalized,
                "status": "already_marked_in_bot",
                "recorded_at": current.isoformat(),
                "attempt_version": _PARTICIPATION_ATTEMPT_VERSION,
            }
''',
    '''            events[token] = {
                "wheel_key": normalized,
                "event_token": token,
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "account_owner": PRIMARY_ACCOUNT_OWNER,
                "account_order": PRIMARY_ACCOUNT_ORDER,
                "status": "already_marked_in_bot",
                "recorded_at": current.isoformat(),
                "attempt_version": _PARTICIPATION_ATTEMPT_VERSION,
            }
''',
    "label already-marked primary",
)
replace_once(
    auto,
    '''        event_record: dict[str, Any] = {
            "wheel_key": normalized,
            "attempted_at": current.isoformat(),
            "status": result.status,
''',
    '''        event_record: dict[str, Any] = {
            "wheel_key": normalized,
            "event_token": token,
            "account_key": PRIMARY_ACCOUNT_KEY,
            "account_label": PRIMARY_ACCOUNT_LABEL,
            "account_owner": PRIMARY_ACCOUNT_OWNER,
            "account_order": PRIMARY_ACCOUNT_ORDER,
            "attempted_at": current.isoformat(),
            "status": result.status,
''',
    "label primary attempt",
)
replace_once(
    auto,
    '''        events[token] = event_record
        entry["auto_participation_status"] = result.status
''',
    '''        event_record = merge_event_record(events.get(token), event_record)
        events[token] = event_record
        entry["auto_participation_status"] = event_record["status"]
''',
    "merge primary attempt monotonically",
)
replace_once(
    auto,
    '''        if not result.success:
            failed += 1
''',
    '''        if _record_success(event_record):
            result = ParticipationResult(True, str(event_record.get("status") or "participated"), str(event_record.get("detail") or result.detail))

        if not result.success:
            failed += 1
''',
    "respect existing success",
)


# Initialize the owner registry before the worker compares event versions.
worker = ROOT / "auto_participation_worker.py"
replace_once(
    worker,
    '''    state = runtime.load_state_without_pending()
    event_versions_before = _event_versions(state)
''',
    '''    state = runtime.load_state_without_pending()
    betboom_auto_participation.canonicalize_primary_event_aliases(state)
    event_versions_before = _event_versions(state)
''',
    "initialize primary account registry",
)


# Secondary account shares the canonical identity and owner registry.
secondary = ROOT / "betboom_account_participation.py"
replace_once(
    secondary,
    'DEFAULT_ALERT_USER = "Вячеслав"\n',
    'DEFAULT_ALERT_USER = "Вячеслав"\nACCOUNT_OWNER = "vyacheslav"\nACCOUNT_ORDER = 20\n',
    "insert secondary owner",
)
replace_function(
    secondary,
    "_base_event_token",
    "_account_event_token",
    '''def _base_event_token(item: dict[str, Any], wheel_key: str = "") -> str:
    key = str(wheel_key or item.get("wheel_key") or item.get("identifier") or "").casefold()
    return primary_auto._event_token(key, item)''',
)
replace_once(
    secondary,
    '''    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
''',
    '''    primary_auto.ensure_default_account_registry(state)
    primary_auto.register_account(
        state,
        account_key=ACCOUNT_KEY,
        account_label=account_label(),
        account_owner=ACCOUNT_OWNER,
        account_order=ACCOUNT_ORDER,
    )
    primary_auto.canonicalize_primary_event_aliases(state)
    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
''',
    "register secondary account",
)
replace_once(
    secondary,
    '''            "alert_user": alert_user(),
            "status": str(result.status),
''',
    '''            "alert_user": alert_user(),
            "account_owner": ACCOUNT_OWNER,
            "account_order": ACCOUNT_ORDER,
            "status": str(result.status),
''',
    "persist secondary owner",
)
replace_once(
    secondary,
    '''        events[token] = record
''',
    '''        events[token] = primary_auto.merge_event_record(events.get(token), record)
''',
    "merge secondary outcome monotonically",
)


# xFLARXx remains a separate owner scope.
xflarxx = ROOT / "xflarxx_account_participation.py"
replace_once(
    xflarxx,
    'DEFAULT_ALERT_USER = "xFLARXx"\n',
    'DEFAULT_ALERT_USER = "xFLARXx"\nACCOUNT_OWNER = "xflarxx"\nACCOUNT_ORDER = 10\n',
    "insert xflarxx owner",
)
replace_once(
    xflarxx,
    '''    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
''',
    '''    account_base.primary_auto.register_account(
        state,
        account_key=ACCOUNT_KEY,
        account_label=account_label(),
        account_owner=ACCOUNT_OWNER,
        account_order=ACCOUNT_ORDER,
    )
    events = state.setdefault("auto_participation_events", {})
    current = monitor.now_utc()
''',
    "register xflarxx account",
)
replace_once(
    xflarxx,
    '''            "alert_user": alert_user(),
            "status": str(result.status),
''',
    '''            "alert_user": alert_user(),
            "account_owner": ACCOUNT_OWNER,
            "account_order": ACCOUNT_ORDER,
            "status": str(result.status),
''',
    "persist xflarxx owner",
)
replace_once(
    xflarxx,
    '''        events[token] = record
''',
    '''        events[token] = account_base.primary_auto.merge_event_record(
            events.get(token), record
        )
''',
    "merge xflarxx outcome monotonically",
)


# Registry-driven aggregation for every enabled account in Vyacheslav's scope.
notifications = ROOT / "auto_participation_notifications.py"
replace_once(
    notifications,
    'AUTO_NOTIFICATION_DESCRIPTION = "Один общий итог по двум BetBoom-аккаунтам"\n',
    'AUTO_NOTIFICATION_DESCRIPTION = "Один общий итог по аккаунтам владельца"\nOWNER_SCOPE = "vyacheslav"\n',
    "make owner aggregation dynamic",
)
account_registry_helper = '''def _expected_accounts(state: dict[str, Any]) -> list[tuple[str, str, int]]:
    registry = state.get("auto_participation_account_registry")
    rows: list[tuple[str, str, int]] = []
    if isinstance(registry, dict):
        for raw_key, raw in registry.items():
            if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
                continue
            if str(raw.get("account_owner") or "") != OWNER_SCOPE:
                continue
            key = str(raw.get("account_key") or raw_key).strip()
            if not key:
                continue
            label = str(raw.get("account_label") or key).strip() or key
            try:
                order = int(raw.get("account_order", 100) or 100)
            except (TypeError, ValueError):
                order = 100
            rows.append((key, label, order))
    if not rows:
        rows = [
            (PRIMARY_ACCOUNT_KEY, PRIMARY_ACCOUNT_LABEL, 10),
            (SECONDARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_LABEL, 20),
        ]
    return sorted(rows, key=lambda row: (row[2], row[0]))


def _account_order(record: dict[str, Any], account_key: str) -> int:
    try:
        return int(record.get("account_order", 10 if account_key == PRIMARY_ACCOUNT_KEY else 20) or 100)
    except (TypeError, ValueError):
        return 100


'''
replace_once(
    notifications,
    "def _parse_datetime(value: Any) -> datetime | None:\n",
    account_registry_helper + "def _parse_datetime(value: Any) -> datetime | None:\n",
    "insert owner account registry",
)
replace_once(
    notifications,
    '''    approved_failures = {
        token: record
''',
    '''    expected_accounts = _expected_accounts(state)
    expected_keys = {key for key, _label, _order in expected_accounts}
    registry_labels = {key: (label, order) for key, label, order in expected_accounts}
    approved_failures = {
        token: record
''',
    "load expected owner accounts",
)
replace_once(
    notifications,
    '''        if account_key not in {PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY}:
            continue
        is_success = _success(raw_record)
''',
    '''        if account_key not in expected_keys:
            continue
        label, order = registry_labels[account_key]
        raw_record = dict(raw_record)
        raw_record.setdefault("account_label", label)
        raw_record.setdefault("account_owner", OWNER_SCOPE)
        raw_record.setdefault("account_order", order)
        is_success = _success(raw_record)
''',
    "filter by owner registry",
)
replace_once(
    notifications,
    '''        if {PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY}.issubset(accounts)
''',
    '''        if expected_keys.issubset(accounts)
''',
    "wait for every enabled owner account",
)
replace_once(
    notifications,
    '''    for account_key in (PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY):
        _token, record, success = accounts[account_key]
''',
    '''    ordered = sorted(
        accounts.items(),
        key=lambda row: (_account_order(row[1][1], row[0]), row[0]),
    )
    for account_key, (_token, record, success) in ordered:
''',
    "render every enabled account",
)


# The notification owner itself persists and dispatches the exact event immediately.
monitor = ROOT / "monitor.py"
immediate_helper = '''def dispatch_notified_wheel_event(state: dict, link: str) -> bool:
    """Persist and dispatch the exact event before the source scan continues."""

    key = wheel_key(link)
    try:
        save_state(state)
        return bool(process_auto_participation_dispatch(state))
    except Exception as exc:
        entry = state.get("active_wheels", {}).get(key)
        if isinstance(entry, dict):
            entry["auto_participation_immediate_dispatch_error"] = (
                f"{type(exc).__name__}: {exc}"
            )[:300]
        print(
            "WARNING immediate auto participation dispatch: "
            f"wheel={key} {type(exc).__name__}: {exc}"
        )
        return False


'''
replace_once(
    monitor,
    "def notify_new_link(\n",
    immediate_helper + "def notify_new_link(\n",
    "insert immediate dispatch owner",
)
replace_once(
    monitor,
    '''            server_start_at=server_start_at,
        )


def notify_activation(
''',
    '''            server_start_at=server_start_at,
        )
        dispatch_notified_wheel_event(state, link)


def notify_activation(
''',
    "dispatch after new notification",
)
replace_once(
    monitor,
    '''            server_start_at=server_start_at,
        )


def fetch_all_sources(
''',
    '''            server_start_at=server_start_at,
        )
        dispatch_notified_wheel_event(state, link)


def fetch_all_sources(
''',
    "dispatch after activation notification",
)


# Tests reproduce zonertg16 and enforce immediate ordering plus dynamic accounts.
test = ROOT / "tests" / "test_auto_participation_consistency.py"
test.write_text(
    '''from __future__ import annotations

from datetime import datetime, timezone

import auto_participation_notifications
import betboom_auto_participation as auto
import monitor

UTC = timezone.utc


def event() -> dict:
    return {
        "wheel_key": "zonertg16",
        "identifier": "zonertg16",
        "action_id": 701,
        "server_start_at": "2026-07-25T08:36:46.419000+00:00",
        "event_id": "6b6a163030b5ef75219f",
        "message_date": "2026-07-25T08:36:49+00:00",
        "url": "https://betboom.ru/freestream/zonertg16",
    }


def test_action_identity_wins_over_internal_event_id() -> None:
    assert auto._event_token("zonertg16", event()) == (
        "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00"
    )


def test_legacy_success_cannot_be_replaced_by_later_failure() -> None:
    state = {
        "active_wheels": {"zonertg16": event()},
        "auto_participation_events": {
            "zonertg16#event:6b6a163030b5ef75219f": {
                "wheel_key": "zonertg16",
                "status": "participated",
                "detail": "post_click_layout:main:Об акции",
                "attempted_at": "2026-07-25T09:05:17+00:00",
                "bot_success_pending_at": "2026-07-25T09:05:35+00:00",
            },
            "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00": {
                "wheel_key": "zonertg16",
                "status": "button_not_found",
                "attempted_at": "2026-07-25T09:06:21+00:00",
                "bot_failure_pending_at": "2026-07-25T09:05:36+00:00",
            },
        },
    }
    assert auto.canonicalize_primary_event_aliases(state)
    token = "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00"
    record = state["auto_participation_events"][token]
    assert record["status"] == "participated"
    assert record["account_key"] == "vyacheslav_primary"
    assert "bot_failure_pending_at" not in record
    assert "zonertg16#event:6b6a163030b5ef75219f" not in state["auto_participation_events"]


def test_owner_registry_waits_for_all_enabled_owner_accounts() -> None:
    base = "wheel#action:42:start"
    state = {
        "auto_participation_account_registry": {
            "vyacheslav_primary": {
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "account_owner": "vyacheslav",
                "account_order": 10,
                "enabled": True,
            },
            "vyacheslav_secondary": {
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "account_owner": "vyacheslav",
                "account_order": 20,
                "enabled": True,
            },
            "vyacheslav_spare": {
                "account_key": "vyacheslav_spare",
                "account_label": "Резервный аккаунт",
                "account_owner": "vyacheslav",
                "account_order": 30,
                "enabled": True,
            },
            "xflarxx_primary": {
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "account_owner": "xflarxx",
                "account_order": 10,
                "enabled": True,
            },
        },
        "active_wheels": {
            "wheel": {"wheel_key": "wheel", "action_id": 42, "server_start_at": "start"}
        },
        "auto_participation_events": {
            base: {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": "vyacheslav_primary",
                "account_label": "Аккаунт 1",
                "status": "participated",
                "bot_success_pending_at": "2026-07-25T09:00:00+00:00",
            },
            base + "#account:vyacheslav_secondary": {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": "vyacheslav_secondary",
                "account_label": "Аккаунт 2",
                "status": "participated",
                "bot_success_pending_at": "2026-07-25T09:00:01+00:00",
            },
            base + "#account:xflarxx_primary": {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": "xflarxx_primary",
                "account_label": "xFLARXx",
                "status": "participated",
                "bot_success_pending_at": "2026-07-25T09:00:02+00:00",
            },
        },
    }
    assert not auto_participation_notifications._settled_event_groups(
        state, now=datetime(2026, 7, 25, 9, 10, tzinfo=UTC)
    )
    state["auto_participation_events"][base + "#account:vyacheslav_spare"] = {
        "wheel_key": "wheel",
        "event_token": base,
        "account_key": "vyacheslav_spare",
        "account_label": "Резервный аккаунт",
        "account_owner": "vyacheslav",
        "account_order": 30,
        "status": "participated",
        "bot_success_pending_at": "2026-07-25T09:00:03+00:00",
    }
    groups = auto_participation_notifications._settled_event_groups(
        state, now=datetime(2026, 7, 25, 9, 10, tzinfo=UTC)
    )
    accounts = groups[base]
    assert set(accounts) == {
        "vyacheslav_primary",
        "vyacheslav_secondary",
        "vyacheslav_spare",
    }
    text, _ = auto_participation_notifications._result_message(
        "wheel", {"identifier": "wheel"}, accounts
    )
    assert "Аккаунт 1" in text
    assert "Аккаунт 2" in text
    assert "Резервный аккаунт" in text
    assert "xFLARXx" not in text


def test_notification_persists_exact_event_before_dispatch(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(monitor, "send_message", lambda *args, **kwargs: calls.append("send") or {"ok": True})

    def save(state: dict) -> None:
        item = state["active_wheels"]["zonertg16"]
        assert item["action_id"] == 701
        assert item["server_start_at"] == "2026-07-25T08:36:46.419000+00:00"
        calls.append("save")

    monkeypatch.setattr(monitor, "save_state", save)
    monkeypatch.setattr(
        monitor,
        "process_auto_participation_dispatch",
        lambda state: calls.append("dispatch") or True,
    )
    message = monitor.Message(
        source="mechanogun",
        message_id=35756,
        date=datetime(2026, 7, 25, 8, 36, 49, tzinfo=UTC),
        text="wheel",
        message_url="https://telegram.me/mechanogun/35756",
    )
    state: dict = {"active_wheels": {}, "button_contexts": {}, "participating_wheels": {}}
    monitor.notify_new_link(
        message,
        "https://betboom.ru/freestream/zonertg16",
        datetime(2026, 7, 25, 18, 36, 46, tzinfo=UTC),
        "активность подтверждена",
        [],
        state,
        action_id=701,
        server_start_at=datetime(2026, 7, 25, 8, 36, 46, 419000, tzinfo=UTC),
    )
    assert calls == ["send", "save", "dispatch"]
''',
    encoding="utf-8",
)


# Persist the new invariant in repository instructions and the changelog.
agents = ROOT / "AGENTS.md"
replace_once(
    agents,
    "объединяет результаты двух аккаунтов, личную отметку владельца, рейтинг и Telegram-уведомление.",
    "объединяет результаты всех включённых аккаунтов одного owner-scope, личную отметку владельца, рейтинг и Telegram-уведомление.",
    "update account aggregation contract",
)
replace_once(
    agents,
    "Автоучастие запускается единственным post-scan dispatcher после сохранения текущего wheel-event в `state.json`; это исключает пустой workflow на старом snapshot.",
    "После успешной первичной доставки точный `wheel_key + action_id + server_start_at` сохраняется в `state.json` и немедленно передаётся единственному dispatcher; post-scan вызов остаётся страховкой. Подтверждённый success монотонен и не может быть заменён более поздним failure другого browser-прохода.",
    "update immediate dispatch contract",
)

changelog = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
text = changelog.read_text(encoding="utf-8")
anchor = "Итог Telegram теперь всегда содержит непустую строку по каждому аккаунту"
addition = '''Доставка и постановка в автоучастие теперь образуют один последовательный
контракт: после успешной карточки точное событие сохраняется и dispatcher
запускается до продолжения долгого source-scan. Конечный post-scan вызов оставлен
только как страховка.

Публичный реестр аккаунтов содержит owner-scope, подпись и порядок. Агрегатор
Вячеслава ждёт все включённые аккаунты его scope, а `xFLARXx` остаётся отдельным
владельцем и не может попасть в чужой итог.

'''
if addition not in text:
    position = text.find(anchor)
    if position < 0:
        raise RuntimeError("changelog auto participation anchor not found")
    text = text[:position] + addition + text[position:]
    changelog.write_text(text, encoding="utf-8")

print("Extended auto participation consistency contract applied")
