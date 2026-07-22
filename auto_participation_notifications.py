from __future__ import annotations

import copy
import html
from datetime import datetime, timezone
from typing import Any, Callable

import auto_participation_owner_sync
import betboom_account_participation
import personal_wheel_voting
import wheel_publications_v2

UTC = timezone.utc
PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"
PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"
SECONDARY_ACCOUNT_KEY = "vyacheslav_secondary"
SECONDARY_ACCOUNT_LABEL = "Аккаунт 2"
XFLARXX_ACCOUNT_KEY = "xflarxx_primary"
XFLARXX_ACCOUNT_LABEL = "xFLARXx"
XFLARXX_ALERT_USER = "xFLARXx"
AUTO_NOTIFICATION_KEY = "auto_participation"
AUTO_NOTIFICATION_LABEL = "🤖 Автоучастие"
AUTO_NOTIFICATION_DESCRIPTION = "Итоги автоматического участия в колёсах"
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
    if key == PRIMARY_ACCOUNT_KEY:
        return key, str(record.get("account_label") or PRIMARY_ACCOUNT_LABEL)
    return key, str(record.get("account_label") or key)


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


def _settled_external_events(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[tuple[str, dict[str, Any], bool]]:
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return []
    approved_failures = {
        token
        for token, _record in auto_participation_owner_sync.pending_failure_events(
            state, now=now
        )
    }
    result: list[tuple[str, dict[str, Any], bool]] = []
    for raw_token, raw_record in events.items():
        if not isinstance(raw_record, dict):
            continue
        if str(raw_record.get("account_key") or "") != XFLARXX_ACCOUNT_KEY:
            continue
        token = str(raw_token)
        success = _success(raw_record)
        if success or token in approved_failures:
            result.append((token, raw_record, success))
    result.sort(key=lambda item: str(item[1].get("attempted_at") or item[0]))
    return result


def _single_result_message(
    key: str,
    item: dict[str, Any],
    record: dict[str, Any],
    success: bool,
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    label = html.escape(str(record.get("account_label") or XFLARXX_ACCOUNT_LABEL))
    if success:
        title = "✅ <b>Участие принято</b>"
        detail = ""
    else:
        title = "⚠️ <b>Участие не принято</b>"
        detail = f"\nПричина: {html.escape(_failure_reason(record))}"
    return (
        f"{title}\n\n"
        f"Колесо: <code>{identifier}</code>\n"
        f"Аккаунт: <b>{label}</b>{detail}",
        _navigation(),
    )


def sync_once(panel: Any) -> dict[str, int]:
    """Deliver one owner aggregate and independent xFLARXx outcomes."""

    snap = panel.snapshot()
    state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
    groups = _settled_event_groups(state)
    external_events = _settled_external_events(state)
    if not groups and not external_events:
        return {
            "pending": 0,
            "completed": 0,
            "failed": 0,
            "success_completed": 0,
            "failure_completed": 0,
            "account_completed": 0,
        }

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

    if groups:
        _access, owner_id, owner, owner_chat_id = auto_participation_owner_sync._owner_context(
            panel
        )
        success_records = auto_participation_owner_sync._completion_records(owner)
        failure_records = auto_participation_owner_sync._failure_records(owner)

        for base_token, accounts in sorted(groups.items()):
            first_record = accounts[PRIMARY_ACCOUNT_KEY][1]
            key = str(first_record.get("wheel_key") or "").casefold()
            item = active.get(key)
            if not key or not isinstance(item, dict):
                failed += 1
                continue
            if auto_participation_owner_sync._event_token(item, key) != base_token:
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
                if any_success:
                    raw_result = panel.mark_personal_participation(key)
                    vote_result = raw_result if isinstance(raw_result, dict) else {}
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

    for token, record, success in external_events:
        key = str(record.get("wheel_key") or "").casefold()
        item = active.get(key)
        if not key or not isinstance(item, dict):
            failed += 1
            continue
        base_token = _base_event_token(token, record)
        if auto_participation_owner_sync._event_token(item, key) != base_token:
            continue
        try:
            _access, user_id, user, chat_id = betboom_account_participation._target_context(
                panel, str(record.get("alert_user") or XFLARXX_ALERT_USER)
            )
            event_key = (
                personal_wheel_voting.wheel_event_key(key, item)
                + f"#account:{XFLARXX_ACCOUNT_KEY}"
            )
            field = (
                "auto_participation_success_events"
                if success
                else "auto_participation_failure_events"
            )
            previous = betboom_account_participation._outcome_records(user, field).get(event_key)
            if _processed(previous):
                continue

            panel.set_context(chat_id, user_id)
            vote_result: dict[str, Any] = {}
            original_button_updated = False
            if success:
                raw_result = panel.mark_personal_participation(key)
                vote_result = raw_result if isinstance(raw_result, dict) else {}
                original_button_updated = auto_participation_owner_sync._mark_original_notification(
                    panel, chat_id, item
                )
            referral_restricted = wheel_publications_v2.entry_is_referral_restricted(item)
            notifications_enabled = _notification_enabled(user)
            should_send = notifications_enabled and (
                success or not referral_restricted
            )
            now_text = datetime.now(UTC).isoformat()
            if should_send:
                text, markup = _single_result_message(key, item, record, success)
                panel.send(text, reply_markup=markup, chat_id=chat_id)
            betboom_account_participation._save_outcome(
                panel,
                user_id,
                field=field,
                event_key=event_key,
                payload={
                    "wheel_key": key,
                    "source_event_token": token,
                    "account_key": XFLARXX_ACCOUNT_KEY,
                    "account_label": str(record.get("account_label") or XFLARXX_ACCOUNT_LABEL),
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
                    "original_button_updated": original_button_updated,
                    "vote_changed": bool(vote_result.get("changed")),
                    "vote_command_id": str(vote_result.get("vote_command_id") or ""),
                },
            )
            if success:
                success_completed += 1
            else:
                failure_completed += 1
            completed += 1
        except Exception as exc:
            failed += 1
            print(
                "WARNING xFLARXx auto participation notification sync: "
                f"wheel={key} {type(exc).__name__}: {exc}"
            )
        finally:
            panel.current_chat_id, panel.current_user_id, panel.current_role = (
                original_context
            )

    return {
        "pending": len(groups) + len(external_events),
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
        def notification_options_for_role(role: str) -> tuple:
            values = list(original_options(role))
            if not any(str(item[0]) == AUTO_NOTIFICATION_KEY for item in values):
                values.append(
                    (
                        AUTO_NOTIFICATION_KEY,
                        AUTO_NOTIFICATION_LABEL,
                        AUTO_NOTIFICATION_DESCRIPTION,
                    )
                )
            return tuple(values)

        panel_class._notification_options_for_role = staticmethod(
            notification_options_for_role
        )

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
    external_state = copy.deepcopy(state)
    external_state["auto_participation_events"][
        base + "#account:xflarxx_primary"
    ] = {
        "wheel_key": "wheel",
        "event_token": base,
        "account_key": XFLARXX_ACCOUNT_KEY,
        "account_label": XFLARXX_ACCOUNT_LABEL,
        "alert_user": XFLARXX_ALERT_USER,
        "status": "participated",
        "bot_success_pending_at": "2026-07-22T12:01:20+00:00",
    }
    external = _settled_external_events(
        external_state, now=datetime(2026, 7, 22, 12, 10, tzinfo=UTC)
    )
    assert len(external) == 1
    assert external[0][1]["account_key"] == XFLARXX_ACCOUNT_KEY
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
    print("unified auto participation notifications self-test passed")


if __name__ == "__main__":
    self_test()
