from __future__ import annotations

import html
import os
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import auto_participation_owner_sync
import betboom_auto_participation
import betboom_account_participation
import bot_private_state
import notification_integrity_v2
import notification_remote_checkpoint
import notification_router
import personal_reminder_filter


_TRANSIENT_PARTICIPATION_STATUSES = {
    "browser_error",
    "unconfirmed",
    "timeout",
    "navigation_timeout",
    "page_timeout",
    "workflow_dispatch_failed",
    "workflow_dispatch_timeout",
    "workflow_dispatch_retry_wait",
}
_TERMINAL_PARTICIPATION_FAILURE_STATUSES = {
    "button_not_found",
    "participation_closed",
    "not_eligible",
    "rejected",
}
_TRANSIENT_PARTICIPATION_MARKERS = (
    "timeouterror",
    "timeout",
    "page.goto",
    "net::",
    "connection",
    "target closed",
    "dispatcher_exit",
    "workflow_dispatch",
    "авторизац",
    "войти",
)


def _transient_participation_failure(
    record: Any,
    entry: dict[str, Any] | None = None,
) -> bool:
    raw = record if isinstance(record, dict) else {}
    current = entry if isinstance(entry, dict) else {}
    status = str(
        raw.get("status")
        or raw.get("bot_failure_status")
        or current.get("auto_participation_status")
        or ""
    ).casefold()
    detail = " ".join(
        str(value or "")
        for value in (
            raw.get("detail"),
            raw.get("bot_failure_detail"),
            raw.get("dispatch_error"),
            current.get("auto_participation_error"),
        )
    ).casefold()
    return status in _TRANSIENT_PARTICIPATION_STATUSES or any(
        marker in detail for marker in _TRANSIENT_PARTICIPATION_MARKERS
    )


def _control_center_authoritative_failure(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
    """Direct failure delivery is forbidden outside the single live Control Center."""

    return False, "control_center_authoritative"


betboom_auto_participation._notify_manual_participation = (
    _control_center_authoritative_failure
)


_original_recoverable_processed_failure = (
    personal_reminder_filter._recoverable_processed_failure
)


def _recoverable_processed_failure(
    record: Any,
    entry: dict[str, Any],
) -> str:
    reason = _original_recoverable_processed_failure(record, entry)
    if reason:
        return reason
    if _transient_participation_failure(record, entry):
        return "transient_browser_or_transport_failure"
    return ""


def _record_dispatch_failure_silently(
    state: dict[str, Any],
    monitor_module: Any,
    token: str,
    wheel_key: str,
    *,
    status: str,
    detail: str,
) -> bool:
    """Retry dispatch transport failures without treating them as a BetBoom outcome."""

    current = monitor_module.now_utc()
    active = state.setdefault("active_wheels", {})
    entry = active.get(wheel_key)
    if not isinstance(entry, dict):
        entry = {"wheel_key": wheel_key, "identifier": wheel_key}
        active[wheel_key] = entry

    dispatches = state.setdefault("auto_participation_dispatch_events", {})
    dispatch_record = dispatches.get(token)
    if not isinstance(dispatch_record, dict):
        dispatch_record = {"wheel_key": wheel_key}
        dispatches[token] = dispatch_record

    processed = state.get("auto_participation_events")
    event_record = processed.get(token) if isinstance(processed, dict) else None
    already_confirmed = (
        bool(entry.get("participating"))
        or str(entry.get("auto_participation_status") or "") == "participated"
        or bool(entry.get("auto_participation_confirmed_at"))
        or (
            isinstance(event_record, dict)
            and str(event_record.get("status") or "")
            in {"participated", "already_marked_participating"}
        )
    )
    if already_confirmed:
        dispatch_record.update(
            {
                "wheel_key": wheel_key,
                "status": "outcome_already_confirmed",
                "last_transport_detail": str(detail or "")[:500],
                "manual_notification_sent": False,
                "user_alert_policy": "forbidden_success_is_authoritative",
            }
        )
        return False

    try:
        previous_failures = int(dispatch_record.get("failure_count", 0) or 0)
    except (TypeError, ValueError):
        previous_failures = 0
    failure_count = previous_failures + 1
    retry_minutes = min(30, max(3, failure_count * 3))
    retry_at = current + timedelta(minutes=retry_minutes)

    dispatch_record.update(
        {
            "wheel_key": wheel_key,
            "status": "workflow_dispatch_retry_wait",
            "dispatch_error": str(detail or "")[:500],
            "last_failure_at": current.isoformat(),
            "retry_after_at": retry_at.isoformat(),
            "failure_count": failure_count,
            "manual_notification_sent": False,
            "user_alert_policy": (
                "forbidden_transport_is_not_participation_outcome"
            ),
        }
    )
    for field in (
        "alert_attempted_at",
        "manual_notification_at",
        "manual_notification_detail",
    ):
        dispatch_record.pop(field, None)

    entry["auto_participation_status"] = "workflow_dispatch_retry_wait"
    entry["auto_participation_checked_at"] = current.isoformat()
    entry["auto_participation_retry_allowed"] = True
    entry["auto_participation_error"] = str(detail or "")[:300]
    entry.pop("auto_participation_manual_notification_at", None)
    entry.pop("auto_participation_manual_notification_error", None)
    return False


personal_reminder_filter._recoverable_processed_failure = (
    _recoverable_processed_failure
)
personal_reminder_filter._record_dispatch_failure = (
    _record_dispatch_failure_silently
)


auto_participation_owner_sync.FAILURE_GRACE_SECONDS = 300
_original_pending_failure_events = auto_participation_owner_sync.pending_failure_events


def _pending_failure_events_authoritative(
    state: dict[str, Any],
    *,
    now: Any = None,
) -> list[tuple[str, dict[str, Any]]]:
    values = _original_pending_failure_events(state, now=now)
    result: list[tuple[str, dict[str, Any]]] = []
    for token, record in values:
        status = str(
            record.get("status") or record.get("bot_failure_status") or ""
        ).casefold()
        if _transient_participation_failure(record):
            continue
        if status not in _TERMINAL_PARTICIPATION_FAILURE_STATUSES:
            continue
        result.append((token, record))
    return result


def _outcome_navigation() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔥 Активные колёса",
                    "callback_data": "bb:l:active",
                },
                {
                    "text": "🏠 Главное меню",
                    "callback_data": "page:menu",
                },
            ]
        ]
    }


def _short_success_message(
    key: str,
    item: dict[str, Any],
    _sources: list[str],
    _weight: int,
    _changed: bool,
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    return (
        "✅ <b>Участие принято</b>\n\n"
        f"Колесо: <code>{identifier}</code>",
        _outcome_navigation(),
    )


def _short_failure_message(
    key: str,
    item: dict[str, Any],
    _record: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    return (
        "⚠️ <b>Участие не принято</b>\n\n"
        f"Колесо: <code>{identifier}</code>",
        _outcome_navigation(),
    )


auto_participation_owner_sync.pending_failure_events = (
    _pending_failure_events_authoritative
)
auto_participation_owner_sync._success_message = _short_success_message
auto_participation_owner_sync._failure_message = _short_failure_message


notification_integrity_v2.install(notification_router)
notification_remote_checkpoint.install(notification_router, notification_integrity_v2)

if "bbvg.bot.runtime" in sys.modules:
    import admin_bot as legacy_admin
    import personal_wheel_voting
    from admin_panel_v2 import TelegramPanelV2
    from bbvg.bot import natural_language_admin
    from bbvg.bot import profile as hunter_profile
    from bbvg.bot.interface import PanelInterfaceRuntime
    from bbvg.bot.sources import SourceRegistryRuntime
    from bbvg.bot.users import UserManagementRuntime

    _previous_profile_handler = personal_wheel_voting.PersonalWheelVotingMixin.handle_callback
    hunter_profile.install(personal_wheel_voting.PersonalWheelVotingMixin)
    _new_profile_handler = personal_wheel_voting.PersonalWheelVotingMixin.handle_callback

    def _combined_profile_handler(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data in {"page:profile", "profile:refresh"}:
            _new_profile_handler(self, query)
            return
        _previous_profile_handler(self, query)

    personal_wheel_voting.PersonalWheelVotingMixin.handle_callback = _combined_profile_handler

    if "compact_menu_rows" in personal_wheel_voting.PersonalWheelVotingMixin.__dict__:
        delattr(personal_wheel_voting.PersonalWheelVotingMixin, "compact_menu_rows")
    if not getattr(UserManagementRuntime, "_bbvg_hunter_profile_menu_installed", False):
        _base_compact_menu_rows = UserManagementRuntime.compact_menu_rows

        def _compact_menu_rows_with_profile(admin: bool) -> list[list[dict[str, Any]]]:
            rows = [list(row) for row in _base_compact_menu_rows(admin)]
            rows.append([{"text": "👤 Мой профиль", "callback_data": "page:profile"}])
            return rows

        UserManagementRuntime.compact_menu_rows = staticmethod(_compact_menu_rows_with_profile)
        UserManagementRuntime._bbvg_hunter_profile_menu_installed = True

    if not getattr(SourceRegistryRuntime, "_bbvg_sources_refresh_removed", False):
        _base_source_menu_rows = SourceRegistryRuntime.source_menu_rows

        def _source_menu_rows_without_registry_refresh(
            admin: bool,
        ) -> list[list[dict[str, Any]]]:
            rows: list[list[dict[str, Any]]] = []
            for row in _base_source_menu_rows(admin):
                filtered = [
                    dict(button)
                    for button in row
                    if str(button.get("callback_data") or "") != "page:sources"
                ]
                if filtered:
                    rows.append(filtered)
            return rows

        SourceRegistryRuntime.source_menu_rows = staticmethod(
            _source_menu_rows_without_registry_refresh
        )
        SourceRegistryRuntime._bbvg_sources_refresh_removed = True

    if not getattr(PanelInterfaceRuntime, "_bbvg_top_find_sources_removed", False):
        _base_period_overview = PanelInterfaceRuntime.period_overview

        def _period_overview_without_top_find_sources(
            self: Any, snap: Any, days: int
        ) -> dict[str, Any]:
            result = dict(_base_period_overview(self, snap, days))
            result["top_sources"] = []
            return result

        PanelInterfaceRuntime.period_overview = _period_overview_without_top_find_sources
        PanelInterfaceRuntime._bbvg_top_find_sources_removed = True

    betboom_account_participation.install_owner_sync()
    auto_participation_owner_sync.install(TelegramPanelV2)
    natural_language_admin.install(legacy_admin.AdminBot)


FAST_MONITOR_INTERVAL_MINUTES = 1


def _with_fast_monitor_interval(access: dict[str, Any]) -> dict[str, Any]:
    settings = access.get("settings")
    if not isinstance(settings, dict):
        settings = {}
        access["settings"] = settings
    settings["monitor_interval_minutes"] = FAST_MONITOR_INTERVAL_MINUTES
    return access


def load_config() -> tuple[dict[str, Any], bool]:
    bundle = bot_private_state.load_file(
        access_default={},
        source_requests_default={"version": 1, "requests": {}},
    )
    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    exists = bool(access.get("owner_id") or access.get("users"))
    if exists:
        return _with_fast_monitor_interval(access), True
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    if not fallback:
        return {}, False
    return {
        "version": 3,
        "owner_id": fallback,
        "admins": [],
        "users": {
            fallback: {
                "id": fallback,
                "chat_id": fallback,
                "notifications_enabled": True,
            }
        },
        "notification_recipients": [fallback],
        "blocked_users": [],
        "settings": {
            "notifications": True,
            "public_panel": True,
            "monitor_interval_minutes": FAST_MONITOR_INTERVAL_MINUTES,
        },
    }, True


def admin_recipients() -> list[str]:
    access, exists = load_config()
    if not exists:
        return []
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    blocked = {str(value) for value in access.get("blocked_users", []) if str(value)}
    admin_ids = {
        str(value)
        for value in [access.get("owner_id"), *access.get("admins", [])]
        if str(value or "") and str(value) not in blocked
    }
    result = {
        str((users.get(user_id) or {}).get("chat_id") or user_id)
        for user_id in admin_ids
        if isinstance(users.get(user_id), dict)
    }
    if result:
        return sorted(value for value in result if value)
    fallback = str(os.getenv("BOT_CHAT_ID", "")).strip()
    return [fallback] if fallback else []


def self_test() -> None:
    original = bot_private_state.STATE_PATH
    try:
        with TemporaryDirectory() as temporary:
            bot_private_state.STATE_PATH = Path(temporary) / "missing-state.enc.json"
            access, exists = load_config()
            assert isinstance(access, dict)
            assert isinstance(exists, bool)
            assert notification_router._bbvg_notification_integrity_v2_installed is True
            assert notification_router._bbvg_remote_notification_checkpoint_installed is True
            assert callable(notification_router.notification_event_identity)
            notification_remote_checkpoint.self_test()

            current_pending_policy = auto_participation_owner_sync.pending_failure_events
            try:
                auto_participation_owner_sync.pending_failure_events = (
                    _original_pending_failure_events
                )
                auto_participation_owner_sync.self_test()
            finally:
                auto_participation_owner_sync.pending_failure_events = (
                    current_pending_policy
                )

            assert betboom_auto_participation._notify_manual_participation(
                None,
                {},
                betboom_auto_participation.ParticipationResult(
                    False, "browser_error", "TimeoutError: Page.goto"
                ),
            ) == (False, "control_center_authoritative")

            transient_state = {
                "auto_participation_events": {
                    "little#action:1:now": {
                        "wheel_key": "little",
                        "status": "unconfirmed",
                        "detail": "кнопка нажата, но подтверждение пока не найдено",
                        "bot_failure_pending_at": "2026-07-21T00:00:00+00:00",
                    }
                }
            }
            assert _pending_failure_events_authoritative(
                transient_state,
                now=auto_participation_owner_sync.datetime(
                    2026, 7, 22, tzinfo=auto_participation_owner_sync.UTC
                ),
            ) == []

            terminal_state = {
                "auto_participation_events": {
                    "wheel#action:2:now": {
                        "wheel_key": "wheel",
                        "status": "button_not_found",
                        "detail": "кнопка участия не найдена после повторной проверки",
                        "bot_failure_pending_at": "2026-07-21T00:00:00+00:00",
                    }
                }
            }
            assert [
                token
                for token, _record in _pending_failure_events_authoritative(
                    terminal_state,
                    now=auto_participation_owner_sync.datetime(
                        2026, 7, 22, tzinfo=auto_participation_owner_sync.UTC
                    ),
                )
            ] == ["wheel#action:2:now"]

            success_text, success_markup = _short_success_message(
                "wheel", {"identifier": "wheel"}, [], 5, True
            )
            failure_text, failure_markup = _short_failure_message(
                "wheel", {"identifier": "wheel"}, {}
            )
            assert "Участие принято" in success_text
            assert "Участие не принято" in failure_text
            assert "bb:l:active" in str(success_markup)
            assert "page:menu" in str(success_markup)
            assert success_markup == failure_markup

            class _Monitor:
                @staticmethod
                def now_utc():
                    return auto_participation_owner_sync.datetime(
                        2026, 7, 22, tzinfo=auto_participation_owner_sync.UTC
                    )

            dispatch_state: dict[str, Any] = {
                "active_wheels": {"wheel": {"identifier": "wheel"}}
            }
            assert not _record_dispatch_failure_silently(
                dispatch_state,
                _Monitor,
                "wheel#action:1:now",
                "wheel",
                status="workflow_dispatch_timeout",
                detail="worker result delayed",
            )
            dispatch = dispatch_state["auto_participation_dispatch_events"][
                "wheel#action:1:now"
            ]
            assert dispatch["status"] == "workflow_dispatch_retry_wait"
            assert dispatch["manual_notification_sent"] is False
            assert "auto_participation_events" not in dispatch_state

            if "bbvg.bot.runtime" in sys.modules:
                source_rows = SourceRegistryRuntime.source_menu_rows(True)
                assert "page:sources" not in str(source_rows)
                assert "page:ranking" in str(source_rows)
    finally:
        bot_private_state.STATE_PATH = original
    print("BB V.G. bot notification state self-test passed")


if __name__ == "__main__":
    self_test()
