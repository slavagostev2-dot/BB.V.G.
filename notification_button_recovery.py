from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from typing import Any

import auto_participation_notifications
import xflarxx_account_participation
import xflarxx_runtime_integration
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41


UTC = timezone.utc
FAST_SYNC_INTERVAL_SECONDS = 5
FAST_FAILURE_GRACE_SECONDS = 30
TERMINAL_FAILURE_STATUSES = {
    "button_not_found",
    "participation_closed",
    "not_eligible",
    "rejected",
}


def _install_fast_outcome_policy() -> None:
    owner_sync = auto_participation_notifications.auto_participation_owner_sync
    if getattr(owner_sync, "_bbvg_fast_outcome_policy_installed", False):
        return

    original_pending_failure_events = owner_sync.pending_failure_events

    def pending_terminal_failure_events(
        state: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        values = original_pending_failure_events(state, now=now)
        return [
            (token, record)
            for token, record in values
            if str(
                record.get("bot_failure_status")
                or record.get("status")
                or ""
            ).casefold()
            in TERMINAL_FAILURE_STATUSES
        ]

    owner_sync.SYNC_INTERVAL_SECONDS = FAST_SYNC_INTERVAL_SECONDS
    owner_sync.FAILURE_GRACE_SECONDS = FAST_FAILURE_GRACE_SECONDS
    owner_sync.pending_failure_events = pending_terminal_failure_events
    owner_sync._bbvg_fast_outcome_policy_installed = True


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

        # Recovery may reconstruct active_wheels after the Telegram message has
        # already been delivered. In that race the old bb:p:<token> button is
        # still valid, but button_contexts can be absent. Rebuild the same stable
        # token from source + message_id + wheel_key and resolve exactly one wheel.
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
auto_participation_notifications.install(TelegramPanelRuntimeButtonRecovery)
xflarxx_account_participation.install_owner_sync()
xflarxx_runtime_integration.install(TelegramPanelRuntimeButtonRecovery)


def self_test() -> None:
    auto_participation_notifications.self_test()
    xflarxx_account_participation.self_test()
    xflarxx_runtime_integration.self_test()
    owner_sync = auto_participation_notifications.auto_participation_owner_sync
    assert owner_sync.SYNC_INTERVAL_SECONDS == FAST_SYNC_INTERVAL_SECONDS
    assert owner_sync.FAILURE_GRACE_SECONDS == FAST_FAILURE_GRACE_SECONDS
    assert getattr(owner_sync, "_bbvg_fast_outcome_policy_installed", False) is True

    terminal_state = {
        "auto_participation_events": {
            "terminal": {
                "status": "button_not_found",
                "bot_failure_pending_at": "2026-07-23T00:00:00+00:00",
            },
            "transient": {
                "status": "unconfirmed",
                "bot_failure_pending_at": "2026-07-23T00:00:00+00:00",
            },
        }
    }
    terminal = owner_sync.pending_failure_events(
        terminal_state,
        now=datetime(2026, 7, 23, 0, 1, tzinfo=UTC),
    )
    assert [token for token, _record in terminal] == ["terminal"]

    assert getattr(
        owner_sync,
        "_bbvg_unified_account_notifications_installed",
        False,
    ) is True
    assert getattr(
        owner_sync,
        "_bbvg_xflarxx_sync_installed",
        False,
    ) is True
    assert TelegramPanelRuntimeButtonRecovery._bbvg_auto_notification_toggle_installed is True
    assert (
        TelegramPanelRuntimeButtonRecovery._bbvg_xflarxx_runtime_integration_installed
        is True
    )
    options = TelegramPanelRuntimeButtonRecovery._notification_options_for_role("owner")
    assert any(str(item[0]) == "auto_participation" for item in options)

    events: list[str] = []
    panel = TelegramPanelRuntimeButtonRecovery.__new__(TelegramPanelRuntimeButtonRecovery)
    panel.mark_personal_participation = lambda key: events.append(str(key))  # type: ignore[method-assign]

    panel.snapshot = lambda force=False: type(  # type: ignore[method-assign]
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
    panel.snapshot = lambda force=False: type(  # type: ignore[method-assign]
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
