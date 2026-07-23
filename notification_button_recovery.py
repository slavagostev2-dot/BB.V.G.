from __future__ import annotations

import argparse
import hashlib
import threading
from typing import Any, Callable

import admin_panel_v2
import auto_participation_backlog_guard
import auto_participation_notifications
import notification_router
import personal_wheel_voting
import wheel_detection_reliability
import xflarxx_account_participation
import xflarxx_runtime_integration
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41


FAST_SYNC_INTERVAL_SECONDS = 5
FAST_CACHE_REFRESH_SECONDS = 5
_AUTO_OUTCOME_DELIVERY_KIND = "auto_participation_outcome"
_outcome_delivery_context = threading.local()


class OutcomeDeliveryBusy(RuntimeError):
    """Another Control Center process currently owns this exact outcome."""


def _install_fast_outcome_policy() -> None:
    owner_sync = auto_participation_notifications.auto_participation_owner_sync
    if getattr(owner_sync, "_bbvg_fast_outcome_policy_installed", False):
        return
    owner_sync.SYNC_INTERVAL_SECONDS = FAST_SYNC_INTERVAL_SECONDS
    admin_panel_v2.CACHE_REFRESH_SECONDS = FAST_CACHE_REFRESH_SECONDS
    owner_sync._bbvg_fast_outcome_policy_installed = True


def _set_outcome_delivery_identity(identity: str) -> None:
    _outcome_delivery_context.identity = str(identity or "")


def _take_outcome_delivery_identity() -> str:
    identity = str(getattr(_outcome_delivery_context, "identity", "") or "")
    _outcome_delivery_context.identity = ""
    return identity


def _delivery_status(key: str) -> str:
    status_reader = getattr(notification_router, "delivery_reservation_status", None)
    if not callable(status_reader):
        return "unknown"
    return str(status_reader(key) or "unknown")


def _send_outcome_once(
    original_send: Callable[..., dict],
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    chat_id: str | None = None,
) -> dict:
    identity = _take_outcome_delivery_identity()
    if not identity:
        return original_send(text, reply_markup=reply_markup, chat_id=chat_id)

    target_chat_id = str(chat_id or "")
    delivery_key = notification_router.delivery_key(
        target_chat_id,
        _AUTO_OUTCOME_DELIVERY_KIND,
        identity,
        None,
    )
    if not notification_router.claim_delivery(delivery_key):
        status = _delivery_status(delivery_key)
        if status == "completed":
            return {
                "ok": True,
                "result": {
                    "suppressed": True,
                    "reason": "automatic_participation_outcome_already_delivered",
                },
            }
        raise OutcomeDeliveryBusy(
            "automatic participation outcome delivery is already claimed"
        )

    try:
        result = original_send(
            text,
            reply_markup=reply_markup,
            chat_id=chat_id,
        )
    except Exception:
        notification_router.release_delivery(delivery_key)
        raise
    else:
        notification_router.complete_delivery(delivery_key)
        return result


def _run_with_outcome_delivery_claims(
    callback: Callable[[Any], dict[str, int]],
    panel: Any,
) -> dict[str, int]:
    original_send = panel.send

    def send_once(
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict:
        return _send_outcome_once(
            original_send,
            text,
            reply_markup=reply_markup,
            chat_id=chat_id,
        )

    _take_outcome_delivery_identity()
    panel.send = send_once
    try:
        value = callback(panel)
        return dict(value) if isinstance(value, dict) else {}
    finally:
        panel.send = original_send
        _take_outcome_delivery_identity()


def _install_auto_outcome_delivery_claims() -> None:
    owner_sync = auto_participation_notifications.auto_participation_owner_sync
    if getattr(owner_sync, "_bbvg_auto_outcome_delivery_claims_installed", False):
        return

    original_result_message = auto_participation_notifications._result_message
    original_xflarxx_message = xflarxx_account_participation._message
    aggregate_sync = auto_participation_notifications.sync_once
    combined_sync = owner_sync.sync_once

    def result_message_with_identity(
        key: str,
        item: dict[str, Any],
        accounts: dict[str, tuple[str, dict[str, Any], bool]],
    ) -> tuple[str, dict[str, Any]]:
        event_key = personal_wheel_voting.wheel_event_key(key, item)
        _set_outcome_delivery_identity(f"owner:{event_key}")
        return original_result_message(key, item, accounts)

    def xflarxx_message_with_identity(
        key: str,
        item: dict[str, Any],
        record: dict[str, Any],
        success: bool,
    ) -> tuple[str, dict[str, Any]]:
        event_key = personal_wheel_voting.wheel_event_key(key, item)
        _set_outcome_delivery_identity(
            f"xflarxx:{event_key}#account:{xflarxx_account_participation.ACCOUNT_KEY}"
        )
        return original_xflarxx_message(key, item, record, success)

    def aggregate_sync_with_claims(panel: Any) -> dict[str, int]:
        return _run_with_outcome_delivery_claims(aggregate_sync, panel)

    def combined_sync_with_claims(panel: Any) -> dict[str, int]:
        return _run_with_outcome_delivery_claims(combined_sync, panel)

    auto_participation_notifications._result_message = result_message_with_identity
    xflarxx_account_participation._message = xflarxx_message_with_identity
    auto_participation_notifications.sync_once = aggregate_sync_with_claims
    owner_sync.sync_once = combined_sync_with_claims
    owner_sync._bbvg_auto_outcome_delivery_claims_installed = True


def _notification_token(key: str, entry: dict[str, Any]) -> str:
    normalized = str(key or entry.get("wheel_key") or entry.get("identifier") or "").casefold()
    source = str(entry.get("source") or "").strip().casefold()
    try:
        message_id = int(entry.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    if not normalized or not source or message_id <= 0:
        return ""
    raw = f"{source}:{message_id}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]


class TelegramPanelRuntimeButtonRecovery(TelegramPanelRuntimeV41):
    """Keep old notification buttons usable even if their saved context was lost."""

    def _mark_personal_from_notification(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        token = data.split(":", 2)[2]
        snap = self.snapshot()
        state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}

        context = state.get("button_contexts", {}).get(token)
        if isinstance(context, dict):
            key = str(
                context.get("wheel_key") or context.get("identifier") or ""
            ).casefold()
            if not key:
                raise ValueError("Не удалось определить колесо")
            self.mark_personal_participation(key)
            return

        matches: list[str] = []
        active = state.get("active_wheels")
        if isinstance(active, dict):
            for key, raw in active.items():
                if not isinstance(raw, dict):
                    continue
                normalized = str(key).casefold()
                stored = str(raw.get("button_token") or "")
                computed = _notification_token(normalized, raw)
                if token and token in {stored, computed}:
                    matches.append(normalized)

        unique = sorted(set(matches))
        if len(unique) != 1:
            raise ValueError("Контекст кнопки устарел")
        self.mark_personal_participation(unique[0])


_install_fast_outcome_policy()
wheel_detection_reliability.install_owner_notification_update()
auto_participation_notifications.install(TelegramPanelRuntimeButtonRecovery)
auto_participation_backlog_guard.install()
xflarxx_account_participation.install_owner_sync()
_install_auto_outcome_delivery_claims()
xflarxx_runtime_integration.install(TelegramPanelRuntimeButtonRecovery)


def self_test() -> None:
    auto_participation_notifications.self_test()
    auto_participation_backlog_guard.self_test()
    xflarxx_account_participation.self_test()
    xflarxx_runtime_integration.self_test()
    owner_sync = auto_participation_notifications.auto_participation_owner_sync
    assert owner_sync.SYNC_INTERVAL_SECONDS == FAST_SYNC_INTERVAL_SECONDS
    assert admin_panel_v2.CACHE_REFRESH_SECONDS == FAST_CACHE_REFRESH_SECONDS
    assert getattr(owner_sync, "_bbvg_fast_outcome_policy_installed", False) is True
    assert getattr(owner_sync, "_bbvg_auto_button_clarity_installed", False) is True
    assert getattr(
        owner_sync,
        "_bbvg_unified_account_notifications_installed",
        False,
    ) is True
    assert getattr(
        owner_sync,
        "_bbvg_stale_backlog_guard_installed",
        False,
    ) is True
    assert getattr(
        owner_sync,
        "_bbvg_xflarxx_sync_installed",
        False,
    ) is True
    assert getattr(
        owner_sync,
        "_bbvg_auto_outcome_delivery_claims_installed",
        False,
    ) is True
    assert TelegramPanelRuntimeButtonRecovery._bbvg_auto_notification_toggle_installed is True
    assert (
        TelegramPanelRuntimeButtonRecovery._bbvg_xflarxx_runtime_integration_installed
        is True
    )
    options = TelegramPanelRuntimeButtonRecovery._notification_options_for_role("owner")
    assert any(str(item[0]) == "auto_participation" for item in options)

    original_delivery_key = notification_router.delivery_key
    original_claim = notification_router.claim_delivery
    original_complete = notification_router.complete_delivery
    original_release = notification_router.release_delivery
    original_status = getattr(notification_router, "delivery_reservation_status", None)
    sent_outcomes: list[str] = []
    completed_outcomes: list[str] = []
    try:
        notification_router.delivery_key = lambda *_args, **_kwargs: "delivery-key"
        notification_router.claim_delivery = lambda _key: True
        notification_router.complete_delivery = completed_outcomes.append
        notification_router.release_delivery = lambda _key: None
        notification_router.delivery_reservation_status = lambda _key: "available"
        _set_outcome_delivery_identity("owner:event-1")
        _send_outcome_once(
            lambda text, **_kwargs: sent_outcomes.append(text) or {"ok": True},
            "result",
            chat_id="1",
        )
        assert sent_outcomes == ["result"]
        assert completed_outcomes == ["delivery-key"]

        notification_router.claim_delivery = lambda _key: False
        notification_router.delivery_reservation_status = lambda _key: "completed"
        _set_outcome_delivery_identity("owner:event-1")
        suppressed = _send_outcome_once(
            lambda text, **_kwargs: sent_outcomes.append(text) or {"ok": True},
            "result",
            chat_id="1",
        )
        assert suppressed["result"]["suppressed"] is True
        assert sent_outcomes == ["result"]

        notification_router.delivery_reservation_status = lambda _key: "claimed"
        _set_outcome_delivery_identity("owner:event-2")
        try:
            _send_outcome_once(
                lambda text, **_kwargs: sent_outcomes.append(text) or {"ok": True},
                "result-2",
                chat_id="1",
            )
        except OutcomeDeliveryBusy:
            pass
        else:
            raise AssertionError("Live claim must postpone the competing outcome sync")
        assert sent_outcomes == ["result"]
    finally:
        notification_router.delivery_key = original_delivery_key
        notification_router.claim_delivery = original_claim
        notification_router.complete_delivery = original_complete
        notification_router.release_delivery = original_release
        if original_status is None:
            delattr(notification_router, "delivery_reservation_status")
        else:
            notification_router.delivery_reservation_status = original_status

    events: list[str] = []
    panel = TelegramPanelRuntimeButtonRecovery.__new__(TelegramPanelRuntimeButtonRecovery)
    panel.mark_personal_participation = lambda key: events.append(str(key))
    panel.snapshot = lambda force=False: type(
        "Snap",
        (),
        {
            "state": {
                "button_contexts": {},
                "active_wheels": {
                    "hooch07": {
                        "source": "hoochcs2",
                        "message_id": 2198,
                        "identifier": "hooch07",
                    }
                },
            }
        },
    )()
    token = _notification_token(
        "hooch07", {"source": "hoochcs2", "message_id": 2198}
    )
    assert token == "cba7abb40c5b77"
    panel._mark_personal_from_notification({"data": f"bb:p:{token}"})
    assert events == ["hooch07"]

    events.clear()
    panel.snapshot = lambda force=False: type(
        "Snap",
        (),
        {
            "state": {
                "button_contexts": {"saved": {"wheel_key": "wheel-b"}},
                "active_wheels": {},
            }
        },
    )()
    panel._mark_personal_from_notification({"data": "bb:p:saved"})
    assert events == ["wheel-b"]
    print("BB V.G. notification participation button recovery self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeButtonRecovery().run()


if __name__ == "__main__":
    raise SystemExit(main())
