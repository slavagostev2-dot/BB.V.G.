from __future__ import annotations

import copy
import html
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import auto_participation_owner_sync
import personal_wheel_voting
import wheel_publications_v2

UTC = timezone.utc
PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"
PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"
SECONDARY_ACCOUNT_KEY = "vyacheslav_secondary"
SECONDARY_ACCOUNT_LABEL = "Аккаунт 2"
AUTO_NOTIFICATION_KEY = "auto_participation"
AUTO_NOTIFICATION_LABEL = "🤖 Автоучастие"
AUTO_NOTIFICATION_DESCRIPTION = "Один общий итог по двум BetBoom-аккаунтам"
RECOVERABLE_OUTCOME_WINDOW = timedelta(hours=12)
SUCCESS_STATUSES = {
    "participated",
    "already_participating",
    "already_marked_participating",
    "already_marked_in_bot",
}
FAILURE_LABELS = {
    "button_not_found": "кнопка участия не найдена",
    "participation_closed": "участие уже закрыто",
    "not_eligible": "аккаунт не подходит",
    "rejected": "BetBoom отклонил участие",
}


def _base_event_token(token: str, record: dict[str, Any]) -> str:
    explicit = str(record.get("event_token") or "").strip()
    if explicit:
        return explicit
    return str(token or "").split("#account:", 1)[0]


def _account_identity(record: dict[str, Any]) -> tuple[str, str]:
    key = str(record.get("account_key") or PRIMARY_ACCOUNT_KEY).strip()
    if key == SECONDARY_ACCOUNT_KEY:
        return key, str(record.get("account_label") or SECONDARY_ACCOUNT_LABEL)
    return PRIMARY_ACCOUNT_KEY, PRIMARY_ACCOUNT_LABEL


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _token_identity(base_token: str) -> tuple[int, str]:
    if "#action:" not in base_token:
        return 0, ""
    tail = base_token.split("#action:", 1)[1]
    action_text, separator, start = tail.partition(":")
    try:
        action_id = int(action_text)
    except (TypeError, ValueError):
        action_id = 0
    return action_id, start if separator else ""


def _group_is_recent(accounts: dict[str, tuple[str, dict[str, Any], bool]]) -> bool:
    timestamps = []
    for _token, record, _success_value in accounts.values():
        for field in ("bot_success_pending_at", "bot_failure_pending_at", "attempted_at"):
            parsed = _parse_datetime(record.get(field))
            if parsed is not None:
                timestamps.append(parsed)
                break
    return bool(timestamps and datetime.now(UTC) - max(timestamps) <= RECOVERABLE_OUTCOME_WINDOW)


def _event_item(
    state: dict[str, Any],
    base_token: str,
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> tuple[dict[str, Any] | None, bool]:
    primary_record = accounts[PRIMARY_ACCOUNT_KEY][1]
    key = str(primary_record.get("wheel_key") or "").casefold()
    active = state.get("active_wheels")
    current = active.get(key) if isinstance(active, dict) else None
    if isinstance(current, dict) and auto_participation_owner_sync._event_token(current, key) == base_token:
        return dict(current), True

    context = primary_record.get("event_context")
    item = dict(context) if isinstance(context, dict) else {}
    if not item:
        candidates = []
        contexts = state.get("button_contexts")
        if isinstance(contexts, dict):
            for raw in contexts.values():
                if not isinstance(raw, dict):
                    continue
                raw_key = str(raw.get("wheel_key") or raw.get("identifier") or "").casefold()
                if raw_key == key:
                    candidates.append(dict(raw))
        _action_id, start_text = _token_identity(base_token)
        start_at = _parse_datetime(start_text)
        if candidates:
            def distance(candidate: dict[str, Any]) -> tuple[float, str]:
                candidate_at = _parse_datetime(candidate.get("message_date") or candidate.get("created_at"))
                if start_at is None or candidate_at is None:
                    return (float("inf"), str(candidate.get("message_date") or ""))
                return (abs((candidate_at - start_at).total_seconds()), candidate_at.isoformat())
            item = min(candidates, key=distance)
    if not item and not key:
        return None, False
    action_id, start_text = _token_identity(base_token)
    item.setdefault("wheel_key", key)
    item.setdefault("identifier", key)
    if action_id > 0:
        item["action_id"] = action_id
    if start_text:
        item["server_start_at"] = start_text
    return item, False


def _success(record: dict[str, Any]) -> bool:
    return str(record.get("status") or "").casefold() in SUCCESS_STATUSES


def _failure_reason(record: dict[str, Any]) -> str:
    status = str(
        record.get("bot_failure_status") or record.get("status") or "failed"
    ).casefold()
    if status in FAILURE_LABELS:
        return FAILURE_LABELS[status]
    detail = str(
        record.get("bot_failure_detail")
        or record.get("detail")
        or "участие не подтверждено"
    ).strip()
    return detail[:120] or "участие не подтверждено"


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
        base_token = _base_event_token(token, raw_record)
        if not base_token:
            continue
        groups.setdefault(base_token, {})[account_key] = (
            token,
            raw_record,
            is_success,
        )
    return {
        token: accounts
        for token, accounts in groups.items()
        if {PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY}.issubset(accounts)
    }


def _notification_enabled(owner: dict[str, Any]) -> bool:
    raw = owner.get("notification_preferences")
    if not isinstance(raw, dict):
        return True
    return bool(raw.get(AUTO_NOTIFICATION_KEY, True))


def _processed(record: Any) -> bool:
    return isinstance(record, dict) and bool(
        record.get("completed_at") or record.get("notified_at")
    )


def _navigation() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "🔥 Активные колёса", "callback_data": "bb:l:active"},
                {"text": "🏠 Главное меню", "callback_data": "page:menu"},
            ]
        ]
    }


def _result_message(
    key: str,
    item: dict[str, Any],
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    primary = accounts[PRIMARY_ACCOUNT_KEY]
    secondary = accounts[SECONDARY_ACCOUNT_KEY]
    all_success = primary[2] and secondary[2]
    if all_success:
        return (
            "✅ <b>Участие принято</b>\n\n"
            f"Колесо: <code>{identifier}</code>\n"
            "Аккаунты: <b>1 и 2</b>",
            _navigation(),
        )

    lines = []
    for account_key in (PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY):
        _token, record, success = accounts[account_key]
        _key, label = _account_identity(record)
        escaped_label = html.escape(label)
        if success:
            lines.append(f"✅ {escaped_label}")
        else:
            lines.append(
                f"❌ {escaped_label} — {html.escape(_failure_reason(record))}"
            )
    title = (
        "⚠️ <b>Автоучастие выполнено не полностью</b>"
        if primary[2] or secondary[2]
        else "⚠️ <b>Участие не принято</b>"
    )
    return (
        f"{title}\n\n"
        f"Колесо: <code>{identifier}</code>\n"
        + "\n".join(lines),
        _navigation(),
    )


def sync_once(panel: Any) -> dict[str, int]:
    """Send at most one owner message after both BetBoom accounts settle."""

    snap = panel.snapshot()
    state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
    groups = _settled_event_groups(state)
    if not groups:
        return {
            "pending": 0,
            "completed": 0,
            "failed": 0,
            "success_completed": 0,
            "failure_completed": 0,
            "account_completed": 0,
        }

    _access, owner_id, owner, owner_chat_id = auto_participation_owner_sync._owner_context(
        panel
    )
    success_records = auto_participation_owner_sync._completion_records(owner)
    failure_records = auto_participation_owner_sync._failure_records(owner)
    active = state.get("active_wheels") if isinstance(state.get("active_wheels"), dict) else {}
    original_context = (
        getattr(panel, "current_chat_id", None),
        getattr(panel, "current_user_id", None),
        getattr(panel, "current_role", "guest"),
    )
    completed = 0
    failed = 0
    success_completed = 0
    failure_completed = 0

    for base_token, accounts in sorted(groups.items()):
        first_record = accounts[PRIMARY_ACCOUNT_KEY][1]
        key = str(first_record.get("wheel_key") or "").casefold()
        item, active_matches = _event_item(state, base_token, accounts)
        if not key or not isinstance(item, dict):
            failed += 1
            continue
        if not active_matches and not _group_is_recent(accounts):
            continue
        event_key = personal_wheel_voting.wheel_event_key(key, item)
        if _processed(success_records.get(event_key)) or _processed(
            failure_records.get(event_key)
        ):
            continue

        all_success = all(value[2] for value in accounts.values())
        any_success = any(value[2] for value in accounts.values())
        referral_restricted = wheel_publications_v2.entry_is_referral_restricted(item)
        notifications_enabled = _notification_enabled(owner)
        should_send = notifications_enabled and (
            all_success or not referral_restricted
        )
        now_text = datetime.now(UTC).isoformat()
        account_payload = {
            account_key: {
                "status": str(record.get("status") or ""),
                "success": bool(success),
                "label": _account_identity(record)[1],
            }
            for account_key, (_token, record, success) in accounts.items()
        }

        try:
            panel.set_context(owner_chat_id, owner_id)
            vote_result: dict[str, Any] = {}
            original_button_updated = False
            if any_success and active_matches:
                raw_result = panel.mark_personal_participation(key)
                vote_result = raw_result if isinstance(raw_result, dict) else {}
            elif any_success:
                vote_result = {"changed": False, "recovered_outcome": True}
            if any_success:
                original_button_updated = auto_participation_owner_sync._mark_original_notification(
                    panel, owner_chat_id, item
                )
            if should_send:
                text, markup = _result_message(key, item, accounts)
                panel.send(text, reply_markup=markup, chat_id=owner_chat_id)

            payload = {
                "wheel_key": key,
                "source_event_token": base_token,
                "completed_at": now_text,
                "notified_at": now_text if should_send else "",
                "notification_sent": should_send,
                "notification_policy": (
                    "sent"
                    if should_send
                    else "disabled"
                    if not notifications_enabled
                    else "referral_failure_suppressed"
                ),
                "referral_restricted": referral_restricted,
                "accounts": account_payload,
                "original_button_updated": original_button_updated,
                "vote_changed": bool(vote_result.get("changed")),
                "recovered_event_context": not active_matches,
                "vote_command_id": str(vote_result.get("vote_command_id") or ""),
            }
            if all_success:
                auto_participation_owner_sync._save_completion(
                    panel, owner_id, event_key, payload
                )
                success_records[event_key] = {"completed_at": now_text}
                success_completed += 1
            else:
                auto_participation_owner_sync._save_failure(
                    panel, owner_id, event_key, payload
                )
                failure_records[event_key] = {"completed_at": now_text}
                failure_completed += 1
            completed += 1
        except Exception as exc:
            failed += 1
            print(
                "WARNING unified auto participation notification sync: "
                f"wheel={key} {type(exc).__name__}: {exc}"
            )
        finally:
            panel.current_chat_id, panel.current_user_id, panel.current_role = (
                original_context
            )

    return {
        "pending": len(groups),
        "completed": completed,
        "failed": failed,
        "success_completed": success_completed,
        "failure_completed": failure_completed,
        "account_completed": completed,
    }


def _patch_panel_notifications(panel_class: type[Any]) -> None:
    if getattr(panel_class, "_bbvg_auto_notification_toggle_installed", False):
        return
    original_preferences: Callable = panel_class.notification_preferences
    original_show: Callable = panel_class.show_notifications
    original_toggle: Callable = panel_class.toggle_notification
    original_options = getattr(panel_class, "_notification_options_for_role", None)

    def notification_preferences(self: Any, user_id: str | None = None) -> dict[str, bool]:
        result = dict(original_preferences(self, user_id))
        target = str(user_id or self.current_user_id or "")
        access = self.load_access()
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(target) if isinstance(users.get(target), dict) else {}
        raw = record.get("notification_preferences") if isinstance(record, dict) else None
        result[AUTO_NOTIFICATION_KEY] = (
            bool(raw.get(AUTO_NOTIFICATION_KEY, True))
            if isinstance(raw, dict)
            else True
        )
        return result

    def show_notifications(self: Any) -> None:
        original_send = self.send

        def send_with_auto(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            prefs = self.notification_preferences()
            line = (
                f"{self.bool_mark(prefs[AUTO_NOTIFICATION_KEY])} "
                f"{AUTO_NOTIFICATION_LABEL} — {AUTO_NOTIFICATION_DESCRIPTION}"
            )
            admin_marker = "\n\n<b>Только для администратора</b>"
            if admin_marker in text:
                text = text.replace(admin_marker, f"\n{line}{admin_marker}", 1)
            else:
                text = text.rstrip() + "\n" + line
            markup = copy.deepcopy(reply_markup) if isinstance(reply_markup, dict) else {}
            rows = markup.get("inline_keyboard")
            rows = list(rows) if isinstance(rows, list) else []
            insert_at = len(rows)
            for index, row in enumerate(rows):
                callbacks = {
                    str(button.get("callback_data") or "")
                    for button in row
                    if isinstance(button, dict)
                }
                if callbacks & {"page:settings", "page:menu"}:
                    insert_at = index
                    break
            rows.insert(
                insert_at,
                [{
                    "text": (
                        f"{self.bool_mark(prefs[AUTO_NOTIFICATION_KEY])} "
                        f"{AUTO_NOTIFICATION_LABEL}"
                    ),
                    "callback_data": f"notify:{AUTO_NOTIFICATION_KEY}",
                }],
            )
            markup["inline_keyboard"] = rows
            return original_send(
                text,
                reply_markup=markup,
                chat_id=chat_id,
            )

        self.send = send_with_auto
        try:
            original_show(self)
        finally:
            self.send = original_send

    def toggle_notification(self: Any, key: str) -> None:
        if key != AUTO_NOTIFICATION_KEY:
            original_toggle(self, key)
            return
        if not self.current_user_id:
            raise PermissionError("Недоступный вид уведомлений")
        access = self.load_access()
        users = access.setdefault("users", {})
        user_id = str(self.current_user_id)
        record = users.get(user_id)
        if not isinstance(record, dict):
            record = {
                "id": user_id,
                "chat_id": str(self.current_chat_id or user_id),
            }
            users[user_id] = record
        raw = record.get("notification_preferences")
        prefs = dict(raw) if isinstance(raw, dict) else {}
        prefs[AUTO_NOTIFICATION_KEY] = not bool(
            prefs.get(AUTO_NOTIFICATION_KEY, True)
        )
        record["notification_preferences"] = prefs
        self.save_access(
            f"Update automatic participation notifications for {user_id} [skip ci]"
        )

    panel_class.notification_preferences = notification_preferences
    panel_class.show_notifications = show_notifications
    panel_class.toggle_notification = toggle_notification

    if callable(original_options):
        def notification_options_for_role(self: Any, role: str) -> tuple:
            values = list(original_options(self, role))
            if not any(str(item[0]) == AUTO_NOTIFICATION_KEY for item in values):
                values.append(
                    (
                        AUTO_NOTIFICATION_KEY,
                        AUTO_NOTIFICATION_LABEL,
                        AUTO_NOTIFICATION_DESCRIPTION,
                    )
                )
            return tuple(values)

        panel_class._notification_options_for_role = notification_options_for_role

    panel_class._bbvg_auto_notification_toggle_installed = True


def install(panel_class: type[Any]) -> None:
    """Replace per-account sends with one event-level outcome and add its toggle."""

    auto_participation_owner_sync.sync_once = sync_once
    auto_participation_owner_sync._bbvg_unified_account_notifications_installed = True
    _patch_panel_notifications(panel_class)


def self_test() -> None:
    base = "wheel#action:42:2026-07-22T12:00:00+00:00"
    state = {
        "auto_participation_events": {
            base: {
                "wheel_key": "wheel",
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:00+00:00",
            },
            base + "#account:vyacheslav_secondary": {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": SECONDARY_ACCOUNT_KEY,
                "account_label": SECONDARY_ACCOUNT_LABEL,
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:10+00:00",
            },
        }
    }
    groups = _settled_event_groups(
        state, now=datetime(2026, 7, 22, 12, 10, tzinfo=UTC)
    )
    assert list(groups) == [base]
    text, _markup = _result_message(
        "wheel", {"identifier": "wheel"}, groups[base]
    )
    assert text.count("Участие принято") == 1
    assert "Аккаунты: <b>1 и 2</b>" in text

    failure_state = copy.deepcopy(state)
    secondary = failure_state["auto_participation_events"][
        base + "#account:vyacheslav_secondary"
    ]
    secondary.pop("bot_success_pending_at", None)
    secondary.update(
        {
            "status": "button_not_found",
            "bot_failure_pending_at": "2026-07-22T12:00:00+00:00",
            "bot_failure_status": "button_not_found",
        }
    )
    groups = _settled_event_groups(
        failure_state, now=datetime(2026, 7, 22, 12, 10, tzinfo=UTC)
    )
    text, _markup = _result_message(
        "wheel", {"identifier": "wheel"}, groups[base]
    )
    assert "❌ Аккаунт 2" in text
    assert "✅ Аккаунт 1" in text
    assert not _notification_enabled(
        {"notification_preferences": {AUTO_NOTIFICATION_KEY: False}}
    )
    assert wheel_publications_v2.entry_is_referral_restricted(
        {"message_text": "Колесо для рефов"}
    )
    recovered_state = {
        "button_contexts": {
            "new": {
                "wheel_key": "wheel",
                "message_date": "2026-07-22T12:00:10+00:00",
                "message_text": "Колесо для рефов",
                "url": "https://betboom.ru/freestream/wheel",
            },
            "old": {
                "wheel_key": "wheel",
                "message_date": "2026-07-21T12:00:10+00:00",
            },
        }
    }
    recovered_item, active_matches = _event_item(recovered_state, base, groups[base])
    assert active_matches is False
    assert recovered_item and recovered_item["action_id"] == 42
    assert wheel_publications_v2.entry_is_referral_restricted(recovered_item)
    print("unified auto participation notifications self-test passed")


if __name__ == "__main__":
    self_test()
