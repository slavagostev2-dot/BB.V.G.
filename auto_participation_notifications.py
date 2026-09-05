from __future__ import annotations

import copy
import html
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import auto_participation_owner_sync
import personal_wheel_voting
import wheel_publications_v2
from bbvg.storage import canonical_account_status

UTC = timezone.utc
PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"
PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"
SECONDARY_ACCOUNT_KEY = "vyacheslav_secondary"
SECONDARY_ACCOUNT_LABEL = "Аккаунт 2"
XFLARXX_ACCOUNT_KEY = "xflarxx_primary"
XFLARXX_ACCOUNT_LABEL = "xFLARXx"
AUTO_NOTIFICATION_KEY = "auto_participation"
AUTO_NOTIFICATION_LABEL = "🤖 Автоучастие"
AUTO_NOTIFICATION_DESCRIPTION = "Один общий итог по аккаунтам владельца"
OWNER_SCOPE = "vyacheslav"
RECOVERABLE_OUTCOME_WINDOW = timedelta(hours=12)
SUCCESS_STATUSES = {
    "participated",
    "already_participating",
    "already_marked_participating",
}
FAILURE_LABELS = {
    "authorization_required": "требуется обновить авторизацию BetBoom",
    "button_not_found": "кнопка участия не найдена",
    "participation_closed": "участие уже закрыто",
    "not_eligible": "аккаунт не подходит",
    "rejected": "BetBoom отклонил участие",
}


def _base_event_token(token: str, record: dict[str, Any]) -> str:
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
    explicit = str(
        record.get("canonical_event_id")
        or record.get("event_id")
        or ""
    ).strip()
    if explicit:
        return explicit
    context = record.get("event_context")
    if isinstance(context, dict):
        contextual = auto_participation_owner_sync._event_token(context)
        if contextual:
            return contextual
    key = str(record.get("wheel_key") or "").casefold()
    active = state.get("active_wheels")
    item = active.get(key) if isinstance(active, dict) else None
    if isinstance(item, dict):
        active_token = auto_participation_owner_sync._event_token(item, key)
        if active_token:
            return active_token
    return base


def _account_identity(record: dict[str, Any]) -> tuple[str, str]:
    key = str(record.get("account_key") or "").strip()
    if not key:
        return "", ""
    if key == SECONDARY_ACCOUNT_KEY:
        return key, str(record.get("account_label") or SECONDARY_ACCOUNT_LABEL)
    if key == PRIMARY_ACCOUNT_KEY:
        return key, str(record.get("account_label") or PRIMARY_ACCOUNT_LABEL)
    return key, str(record.get("account_label") or key)


def _expected_accounts(state: dict[str, Any]) -> list[tuple[str, str, int]]:
    registry = state.get("auto_participation_account_registry")
    rows: list[tuple[str, str, int]] = []
    if isinstance(registry, dict):
        for raw_key, raw in registry.items():
            if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
                continue
            key = str(raw.get("account_key") or raw_key).strip()
            if key not in {
                PRIMARY_ACCOUNT_KEY,
                SECONDARY_ACCOUNT_KEY,
                XFLARXX_ACCOUNT_KEY,
            }:
                continue
            label = str(raw.get("account_label") or key).strip() or key
            try:
                order = int(raw.get("account_order", 100) or 100)
            except (TypeError, ValueError):
                order = 100
            rows.append((key, label, order))
    configured = {key for key, _label, _order in rows}
    for key, label, order in (
        (PRIMARY_ACCOUNT_KEY, PRIMARY_ACCOUNT_LABEL, 10),
        (SECONDARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_LABEL, 20),
        (XFLARXX_ACCOUNT_KEY, XFLARXX_ACCOUNT_LABEL, 30),
    ):
        if key not in configured:
            rows.append((key, label, order))
    return sorted(rows, key=lambda row: (row[2], row[0]))


def _account_order(record: dict[str, Any], account_key: str) -> int:
    fixed = {
        PRIMARY_ACCOUNT_KEY: 10,
        SECONDARY_ACCOUNT_KEY: 20,
        XFLARXX_ACCOUNT_KEY: 30,
    }
    if account_key in fixed:
        return fixed[account_key]
    try:
        return int(record.get("account_order", 10 if account_key == PRIMARY_ACCOUNT_KEY else 20) or 100)
    except (TypeError, ValueError):
        return 100


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


def _prefer_outcome(
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
    expected_accounts = _expected_accounts(state)
    expected_keys = {key for key, _label, _order in expected_accounts}
    registry_labels = {key: (label, order) for key, label, order in expected_accounts}
    approved_failures = {
        token: record
        for token, record in auto_participation_owner_sync.pending_failure_events(
            state, now=now
        )
    }
    groups: dict[str, dict[str, tuple[str, dict[str, Any], bool]]] = {}
    observed_groups: dict[
        str, dict[str, tuple[str, dict[str, Any], bool]]
    ] = {}
    for raw_token, raw_record in events.items():
        if not isinstance(raw_record, dict):
            continue
        token = str(raw_token)
        account_key, _label = _account_identity(raw_record)
        if account_key not in expected_keys:
            continue
        label, order = registry_labels[account_key]
        raw_record = dict(raw_record)
        raw_record.setdefault("account_label", label)
        raw_record.setdefault("account_owner", OWNER_SCOPE)
        raw_record.setdefault("account_order", order)
        is_success = _success(raw_record)
        base_token = _canonical_event_token(state, token, raw_record)
        if not base_token:
            continue
        incoming = (token, raw_record, is_success)
        observed = observed_groups.setdefault(base_token, {}).get(account_key)
        observed_groups[base_token][account_key] = _prefer_outcome(
            observed, incoming
        )
        if not is_success and token not in approved_failures:
            continue
        current = groups.setdefault(base_token, {}).get(account_key)
        groups[base_token][account_key] = _prefer_outcome(current, incoming)
    settled = {
        token: accounts
        for token, accounts in groups.items()
        if expected_keys.issubset(accounts)
    }
    for token, accounts in observed_groups.items():
        if not expected_keys.issubset(accounts):
            continue
        item, _active_matches = _event_item(state, token, accounts)
        if (
            isinstance(item, dict)
            and wheel_publications_v2.entry_is_referral_restricted(item)
        ):
            settled[token] = accounts
    return settled


def _notification_enabled(owner: dict[str, Any]) -> bool:
    raw = owner.get("notification_preferences")
    if not isinstance(raw, dict):
        return True
    return bool(raw.get(AUTO_NOTIFICATION_KEY, True))


def _should_send_notification(owner: dict[str, Any], item: dict[str, Any]) -> bool:
    return _notification_enabled(owner)


def _should_send_event_result(
    owner: dict[str, Any],
    item: dict[str, Any],
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> bool:
    """Send one result whenever the recipient enabled auto-participation reports."""

    return _notification_enabled(owner)


def _processed(record: Any, *, allow_referral_upgrade: bool = False) -> bool:
    if not isinstance(record, dict):
        return False
    if (
        allow_referral_upgrade
        and not record.get("notified_at")
        and record.get("notification_policy") == "referral_suppressed"
    ):
        return False
    return bool(record.get("completed_at") or record.get("notified_at"))


def _should_finalize(
    success_record: Any,
    failure_record: Any,
    *,
    all_success: bool,
    allow_referral_upgrade: bool = False,
) -> bool:
    if _processed(
        success_record,
        allow_referral_upgrade=allow_referral_upgrade,
    ):
        return False
    if (
        _processed(
            failure_record,
            allow_referral_upgrade=allow_referral_upgrade,
        )
        and not all_success
    ):
        return False
    return True


def _navigation() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "🔥 Активные колёса", "callback_data": "bb:l:active"},
                {"text": "🏠 Главное меню", "callback_data": "page:menu"},
            ]
        ]
    }


def _success_description(record: dict[str, Any]) -> str:
    detail = str(record.get("detail") or "").casefold()
    if "post_click_layout" in detail or "об акции" in detail:
        return "участие подтверждено изменением страницы BetBoom"
    if str(record.get("status") or "").casefold() in {"already_participating", "already_marked_participating"}:
        return "участие уже было принято ранее"
    return "участие подтверждено BetBoom"


def _account_result_status(record: dict[str, Any], success: bool) -> str:
    if success:
        return "participated"
    raw_status = str(
        record.get("bot_failure_status") or record.get("status") or "unconfirmed"
    )
    if raw_status.strip().casefold() == "authorization_required":
        return "authorization_required"
    confirmation = str(
        record.get("confirmation")
        or record.get("confirmation_method")
        or ""
    )
    detail = str(
        record.get("bot_failure_detail")
        or record.get("error_text")
        or record.get("detail")
        or ""
    )
    return canonical_account_status(raw_status, confirmation, detail)


def _result_message(
    key: str,
    item: dict[str, Any],
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    all_success = all(value[2] for value in accounts.values())
    any_success = any(value[2] for value in accounts.values())
    referral_restricted = wheel_publications_v2.entry_is_referral_restricted(item)
    lines: list[str] = []
    ordered = sorted(
        accounts.items(),
        key=lambda row: (_account_order(row[1][1], row[0]), row[0]),
    )
    for account_key, (_token, record, success) in ordered:
        _key, label = _account_identity(record)
        fallback = (
            PRIMARY_ACCOUNT_LABEL
            if account_key == PRIMARY_ACCOUNT_KEY
            else SECONDARY_ACCOUNT_LABEL
            if account_key == SECONDARY_ACCOUNT_KEY
            else account_key
        )
        escaped_label = html.escape(label or fallback)
        result_status = _account_result_status(record, success)
        if result_status == "participated":
            lines.append(
                f"✅ {escaped_label} — {html.escape(_success_description(record))}"
            )
        elif result_status == "authorization_required":
            lines.append(
                f"🔐 {escaped_label} — требуется обновить авторизацию BetBoom; "
                "автоповторы остановлены"
            )
        elif result_status == "referral_ineligible":
            lines.append(
                f"⛔ {escaped_label} — недоступно: BetBoom подтвердил "
                "реферальное ограничение"
            )
        elif result_status == "expired":
            lines.append(f"⌛ {escaped_label} — подтверждено, что колесо завершено")
        elif result_status == "technical_error":
            lines.append(
                f"🛠 {escaped_label} — техническая ошибка: "
                f"{html.escape(_failure_reason(record))}; повторная проверка запланирована"
            )
        else:
            lines.append(
                f"⚠️ {escaped_label} — результат не подтверждён; "
                "повторная проверка запланирована"
            )
    wheel_type = wheel_publications_v2.referral_classification(item)
    if wheel_type == wheel_publications_v2.WHEEL_TYPE_REFERRAL:
        title = "🎡 <b>Реферальное колесо</b>"
    else:
        title = (
            "✅ <b>Участие принято</b>"
            if all_success
            else "⚠️ <b>Автоучастие выполнено не полностью</b>"
            if any_success
            else "⚠️ <b>Участие не принято</b>"
        )
    source = str(item.get("source") or "").strip().lstrip("@")
    source_line = f"Источник: @{html.escape(source)}\n" if source else ""
    return (
        f"{title}\n\n"
        f"{source_line}"
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
        all_success = all(value[2] for value in accounts.values())
        any_success = any(value[2] for value in accounts.values())
        referral_restricted = wheel_publications_v2.entry_is_referral_restricted(item)
        allow_referral_upgrade = referral_restricted
        if not _should_finalize(
            success_records.get(event_key),
            failure_records.get(event_key),
            all_success=all_success,
            allow_referral_upgrade=allow_referral_upgrade,
        ):
            continue

        notifications_enabled = _notification_enabled(owner)
        should_send = _should_send_event_result(owner, item, accounts)
        now_text = datetime.now(UTC).isoformat()
        account_payload = {
            account_key: {
                "status": _account_result_status(record, success),
                "raw_status": str(record.get("status") or ""),
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
                    "referral_result_sent"
                    if should_send and referral_restricted
                    else "sent"
                    if should_send
                    else "disabled"
                    if not notifications_enabled
                    else "not_sent"
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
                "account_key": PRIMARY_ACCOUNT_KEY,
                "account_label": PRIMARY_ACCOUNT_LABEL,
                "event_token": base,
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
            base + "#account:xflarxx_primary": {
                "wheel_key": "wheel",
                "event_token": base,
                "account_key": XFLARXX_ACCOUNT_KEY,
                "account_label": XFLARXX_ACCOUNT_LABEL,
                "status": "participated",
                "bot_success_pending_at": "2026-07-22T12:01:20+00:00",
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
    assert "✅ Аккаунт 1 — участие подтверждено BetBoom" in text
    assert "✅ Аккаунт 2 — участие подтверждено BetBoom" in text
    assert "✅ xFLARXx — участие подтверждено BetBoom" in text
    state["auto_participation_events"][
        base + "#account:xflarxx_primary"
    ] = {
        "wheel_key": "wheel",
        "event_token": base,
        "account_key": "xflarxx_primary",
        "account_label": "xFLARXx",
        "status": "button_not_found",
        "bot_failure_pending_at": "2026-07-22T12:01:20+00:00",
        "bot_failure_status": "button_not_found",
    }
    isolated_groups = _settled_event_groups(
        state, now=datetime(2026, 7, 22, 12, 10, tzinfo=UTC)
    )
    assert isolated_groups[base][PRIMARY_ACCOUNT_KEY][0] == base
    assert isolated_groups[base][PRIMARY_ACCOUNT_KEY][2] is True
    assert isolated_groups[base][XFLARXX_ACCOUNT_KEY][2] is False
    assert _should_finalize(
        {},
        {"notified_at": "2026-07-22T12:02:00+00:00"},
        all_success=True,
    )
    assert not _should_finalize(
        {},
        {"notified_at": "2026-07-22T12:02:00+00:00"},
        all_success=False,
    )

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
    assert "⚠️ Аккаунт 2" in text
    assert "✅ Аккаунт 1" in text

    auth_state = copy.deepcopy(failure_state)
    auth_secondary = auth_state["auto_participation_events"][
        base + "#account:vyacheslav_secondary"
    ]
    auth_secondary.update(
        {
            "status": "authorization_required",
            "bot_failure_status": "authorization_required",
            "bot_failure_detail": "страница показывает вход/авторизацию",
        }
    )
    auth_groups = _settled_event_groups(
        auth_state, now=datetime(2026, 7, 22, 12, 10, tzinfo=UTC)
    )
    auth_text, _auth_markup = _result_message(
        "wheel", {"identifier": "wheel"}, auth_groups[base]
    )
    assert (
        "🔐 Аккаунт 2 — требуется обновить авторизацию BetBoom; автоповторы остановлены"
        in auth_text
    )
    assert "Аккаунт 2 — результат не подтверждён" not in auth_text

    assert not _notification_enabled(
        {"notification_preferences": {AUTO_NOTIFICATION_KEY: False}}
    )
    assert _should_send_notification(
        {"notification_preferences": {}},
        {"identifier": "ordinary"},
    )
    assert _should_send_notification(
        {"notification_preferences": {}},
        {"message_text": "Колесо для рефов"},
    )
    assert _should_send_event_result(
        {"notification_preferences": {}},
        {"message_text": "Колесо для рефов"},
        groups[base],
    )
    assert _should_send_event_result(
        {"notification_preferences": {}},
        {"message_text": "Колесо для рефов"},
        {
            key: (token, record, False)
            for key, (token, record, _success_value) in groups[base].items()
        },
    )
    assert _should_finalize(
        {},
        {
            "completed_at": "2026-07-22T12:02:00+00:00",
            "notification_policy": "referral_suppressed",
        },
        all_success=False,
        allow_referral_upgrade=True,
    )
    assert not wheel_publications_v2.entry_is_referral_restricted(
        {"message_text": "Колесо для рефов"}
    )
    assert wheel_publications_v2.entry_is_referral_restricted(
        {
            "wheel_type": wheel_publications_v2.WHEEL_TYPE_REFERRAL,
            "referral_classification_evidence": (
                wheel_publications_v2.STRONG_REFERRAL_EVIDENCE
            ),
        }
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
    assert not wheel_publications_v2.entry_is_referral_restricted(recovered_item)
    zonertg16_state = {
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
            "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00#account:xflarxx_primary": {
                "wheel_key": "zonertg16",
                "account_key": XFLARXX_ACCOUNT_KEY,
                "account_label": XFLARXX_ACCOUNT_LABEL,
                "event_token": "zonertg16#action:701:2026-07-25T08:36:46.419000+00:00",
                "status": "participated",
                "attempted_at": "2026-07-25T09:05:55+00:00",
                "bot_success_pending_at": "2026-07-25T09:05:55+00:00",
            },
        },
    }
    grouped = _settled_event_groups(
        zonertg16_state,
        now=datetime(2026, 7, 25, 9, 10, tzinfo=UTC),
    )
    event_id = auto_participation_owner_sync._event_token(
        zonertg16_state["active_wheels"]["zonertg16"],
        "zonertg16",
    )
    event = grouped[event_id]
    assert event[PRIMARY_ACCOUNT_KEY][2] is True
    assert event[SECONDARY_ACCOUNT_KEY][2] is True
    assert event[XFLARXX_ACCOUNT_KEY][2] is True
    text, _markup = _result_message("zonertg16", {"identifier": "zonertg16"}, event)
    assert "Аккаунт 1 — участие подтверждено BetBoom" in text
    assert "Аккаунт 2 — участие подтверждено изменением страницы BetBoom" in text
    assert "кнопка участия не найдена" not in text
    assert _account_identity({}) == ("", "")
    print("auto participation notifications self-test passed")


if __name__ == "__main__":
    self_test()
