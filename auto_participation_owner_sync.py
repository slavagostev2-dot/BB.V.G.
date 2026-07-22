from __future__ import annotations

import html
import threading
from datetime import datetime, timezone
from typing import Any

import notification_integrity_v2
import personal_wheel_voting

UTC = timezone.utc
SYNC_INTERVAL_SECONDS = 20
FAILURE_GRACE_SECONDS = 90
MAX_COMPLETED_EVENTS = 500


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _event_token(
    item: dict[str, Any],
    wheel_key: str = "",
) -> str:
    key = str(
        wheel_key
        or item.get("wheel_key")
        or item.get("identifier")
        or ""
    ).casefold()
    try:
        action_id = int(item.get("action_id") or 0)
    except (TypeError, ValueError):
        action_id = 0
    start = str(item.get("server_start_at") or "")
    if action_id > 0:
        return f"{key}#action:{action_id}:{start}"
    return f"{key}#seen:{item.get('message_date') or ''}"


def pending_events(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return only success outcomes explicitly queued for the live Control Center."""

    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for token, raw in events.items():
        if not isinstance(raw, dict):
            continue
        if not raw.get("bot_success_pending_at"):
            continue
        if str(raw.get("status") or "") != "participated":
            continue
        result.append((str(token), raw))
    result.sort(key=lambda item: str(item[1].get("bot_success_pending_at") or ""))
    return result


def pending_failure_events(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return settled failure candidates; direct workflow/recovery sends are forbidden."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    events = state.get("auto_participation_events")
    if not isinstance(events, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for token, raw in events.items():
        if not isinstance(raw, dict):
            continue
        pending_at = _parse_datetime(raw.get("bot_failure_pending_at"))
        if pending_at is None:
            continue
        if (current - pending_at).total_seconds() < FAILURE_GRACE_SECONDS:
            continue
        if bool(raw.get("manual_notification_sent")):
            continue
        if raw.get("bot_success_pending_at"):
            continue
        if str(raw.get("status") or "") in {
            "participated",
            "already_marked_participating",
        }:
            continue
        result.append((str(token), raw))
    result.sort(key=lambda item: str(item[1].get("bot_failure_pending_at") or ""))
    return result


def _outcome_records(owner: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    raw = owner.get(field)
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): dict(value) if isinstance(value, dict) else {}
        for key, value in raw.items()
        if str(key)
    }


def _completion_records(owner: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _outcome_records(owner, "auto_participation_success_events")


def _failure_records(owner: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _outcome_records(owner, "auto_participation_failure_events")


def _save_outcome(
    panel: Any,
    owner_id: str,
    *,
    field: str,
    event_key: str,
    payload: dict[str, Any],
    commit_message: str,
) -> None:
    access = panel.load_access(force=True)
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    owner = users.get(owner_id) if isinstance(users.get(owner_id), dict) else None
    if not isinstance(owner, dict):
        raise RuntimeError("Профиль владельца BB V.G. не найден при фиксации автоучастия")
    completed = _outcome_records(owner, field)
    completed[event_key] = dict(payload)
    if len(completed) > MAX_COMPLETED_EVENTS:
        completed = dict(list(completed.items())[-MAX_COMPLETED_EVENTS:])
    owner[field] = completed
    panel.access = access
    panel.access_loaded = True
    panel.save_access(commit_message)


def _save_completion(
    panel: Any,
    owner_id: str,
    event_key: str,
    payload: dict[str, Any],
) -> None:
    _save_outcome(
        panel,
        owner_id,
        field="auto_participation_success_events",
        event_key=event_key,
        payload=payload,
        commit_message="Record automatic participation success for owner [skip ci]",
    )


def _save_failure(
    panel: Any,
    owner_id: str,
    event_key: str,
    payload: dict[str, Any],
) -> None:
    _save_outcome(
        panel,
        owner_id,
        field="auto_participation_failure_events",
        event_key=event_key,
        payload=payload,
        commit_message="Record automatic participation failure for owner [skip ci]",
    )


def _mark_original_notification(
    panel: Any,
    owner_chat_id: str,
    item: dict[str, Any],
) -> bool:
    button_token = str(item.get("button_token") or "").strip().casefold()
    if not button_token:
        return False
    try:
        message_id = notification_integrity_v2.participation_message_id(
            owner_chat_id, button_token
        )
    except Exception as exc:
        print(
            "WARNING participation notification lookup failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False
    if not message_id:
        return False
    url = str(item.get("url") or "").strip()
    button: dict[str, Any] = {
        "text": "✅ Участвую",
        "callback_data": f"bb:p:{button_token}",
    }
    if url:
        button = {"text": "✅ Участвую", "url": url}
    try:
        response = panel.telegram_api(
            "editMessageReplyMarkup",
            {
                "chat_id": owner_chat_id,
                "message_id": int(message_id),
                "reply_markup": {"inline_keyboard": [[button]]},
            },
        )
    except Exception as exc:
        print(
            "WARNING automatic participation button update failed: "
            f"message_id={message_id} {type(exc).__name__}: {exc}"
        )
        return False
    return bool(response.get("ok", True)) if isinstance(response, dict) else True


def _success_message(
    key: str,
    item: dict[str, Any],
    sources: list[str],
    weight: int,
    changed: bool,
) -> tuple[str, dict[str, Any] | None]:
    identifier = html.escape(str(item.get("identifier") or key))
    source_text = ", ".join(f"@{html.escape(source)}" for source in sources[:8])
    if len(sources) > 8:
        source_text += f" и ещё {len(sources) - 8}"
    mark_text = (
        "✅ Отметка «Участвую» в BB V.G. поставлена автоматически."
        if changed
        else "✅ Отметка «Участвую» уже была поставлена ранее — повторный голос не создавался."
    )
    text = (
        "✅ <b>Автоучастие в колесе BetBoom подтверждено</b>\n\n"
        f"Колесо: <code>{identifier}</code>\n"
        "BetBoom подтвердил участие.\n"
        f"{mark_text}\n"
        f"🏆 Рейтинг: применён штатный голос владельца — <b>{weight} очков</b> "
        "каждому уникальному источнику этого события. Повторное начисление для того же события исключено."
    )
    if source_text:
        text += f"\n📡 Источники: {source_text}"
    url = str(item.get("url") or "").strip()
    markup = (
        {"inline_keyboard": [[{"text": "🎡 Открыть колесо", "url": url}]]}
        if url
        else None
    )
    return text, markup


def _failure_message(
    key: str,
    item: dict[str, Any],
    record: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    identifier = html.escape(str(item.get("identifier") or key))
    detail = html.escape(
        str(
            record.get("bot_failure_detail")
            or record.get("detail")
            or "BetBoom не подтвердил автоматическое участие"
        )[:300]
    )
    text = (
        "⚠️ <b>Автоучастие в колесе BetBoom не подтверждено</b>\n\n"
        f"Колесо: <code>{identifier}</code>\n"
        f"Причина: {detail}\n\n"
        "Автоматическое участие считается неуспешным только после финальной проверки Control Center. "
        "Откройте колесо и при необходимости нажмите «Участвовать» вручную."
    )
    url = str(item.get("url") or "").strip()
    markup = (
        {"inline_keyboard": [[{"text": "🎡 Открыть колесо", "url": url}]]}
        if url
        else None
    )
    return text, markup


def _owner_context(panel: Any) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    access = panel.load_access(force=True)
    owner_id = str(access.get("owner_id") or "").strip()
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    owner = users.get(owner_id) if isinstance(users.get(owner_id), dict) else None
    if not owner_id or not isinstance(owner, dict):
        raise RuntimeError("В зашифрованном состоянии BB V.G. не найден владелец")
    owner_chat_id = str(owner.get("chat_id") or owner_id).strip()
    if not owner_chat_id:
        raise RuntimeError("У владельца BB V.G. отсутствует Telegram chat_id")
    return access, owner_id, owner, owner_chat_id


def sync_once(panel: Any) -> dict[str, int]:
    """Finalize success and failure outcomes through one authoritative Telegram writer."""

    snap = panel.snapshot()
    state = snap.state if isinstance(getattr(snap, "state", None), dict) else {}
    success_candidates = pending_events(state)
    failure_candidates = pending_failure_events(state)
    if not success_candidates and not failure_candidates:
        return {
            "pending": 0,
            "completed": 0,
            "failed": 0,
            "success_completed": 0,
            "failure_completed": 0,
        }

    _access, owner_id, owner, owner_chat_id = _owner_context(panel)
    success_records = _completion_records(owner)
    failure_records = _failure_records(owner)
    active = state.get("active_wheels") if isinstance(state.get("active_wheels"), dict) else {}
    completed_count = 0
    failed_count = 0
    success_completed = 0
    failure_completed = 0
    original_context = (
        getattr(panel, "current_chat_id", None),
        getattr(panel, "current_user_id", None),
        getattr(panel, "current_role", "guest"),
    )

    # Success always wins and is processed first. A later public-state regression
    # cannot produce a failure because encrypted success completion is authoritative.
    for token, record in success_candidates:
        key = str(record.get("wheel_key") or "").casefold()
        item = active.get(key)
        if not key or not isinstance(item, dict):
            failed_count += 1
            continue
        if _event_token(item, key) != token:
            continue
        event_key = personal_wheel_voting.wheel_event_key(key, item)
        previous = success_records.get(event_key)
        if isinstance(previous, dict) and previous.get("notified_at"):
            continue

        try:
            panel.set_context(owner_chat_id, owner_id)
            result = panel.mark_personal_participation(key)
            changed = bool(result.get("changed")) if isinstance(result, dict) else False
            try:
                weight = int(result.get("weight", 5)) if isinstance(result, dict) else 5
            except (TypeError, ValueError):
                weight = 5
            sources = panel._sources_for_item(snap, key, item)
            original_button_updated = _mark_original_notification(
                panel, owner_chat_id, item
            )
            text, markup = _success_message(key, item, sources, weight, changed)
            panel.send(text, reply_markup=markup, chat_id=owner_chat_id)
            now_text = datetime.now(UTC).isoformat()
            _save_completion(
                panel,
                owner_id,
                event_key,
                {
                    "wheel_key": key,
                    "source_event_token": token,
                    "notified_at": now_text,
                    "rating_weight": weight,
                    "original_button_updated": original_button_updated,
                    "vote_command_id": (
                        str(result.get("vote_command_id") or "")
                        if isinstance(result, dict)
                        else ""
                    ),
                },
            )
            success_records[event_key] = {"notified_at": now_text}
            completed_count += 1
            success_completed += 1
        except Exception as exc:
            failed_count += 1
            print(
                "WARNING auto participation owner success sync: "
                f"wheel={key} {type(exc).__name__}: {exc}"
            )
        finally:
            panel.current_chat_id, panel.current_user_id, panel.current_role = original_context

    for token, record in failure_candidates:
        key = str(record.get("wheel_key") or "").casefold()
        item = active.get(key)
        if not key or not isinstance(item, dict):
            continue
        if _event_token(item, key) != token:
            continue

        # Revalidate the exact current event after the grace period. Any BetBoom
        # confirmation or queued/sent success permanently suppresses a failure.
        if bool(item.get("participating")):
            continue
        if str(item.get("auto_participation_status") or "") == "participated":
            continue
        if item.get("auto_participation_confirmed_at"):
            continue
        if record.get("bot_success_pending_at"):
            continue
        if str(record.get("status") or "") in {
            "participated",
            "already_marked_participating",
        }:
            continue
        if bool(record.get("manual_notification_sent")):
            continue

        event_key = personal_wheel_voting.wheel_event_key(key, item)
        success_previous = success_records.get(event_key)
        if isinstance(success_previous, dict) and success_previous.get("notified_at"):
            continue
        failure_previous = failure_records.get(event_key)
        if isinstance(failure_previous, dict) and failure_previous.get("notified_at"):
            continue

        try:
            text, markup = _failure_message(key, item, record)
            panel.send(text, reply_markup=markup, chat_id=owner_chat_id)
            now_text = datetime.now(UTC).isoformat()
            _save_failure(
                panel,
                owner_id,
                event_key,
                {
                    "wheel_key": key,
                    "source_event_token": token,
                    "notified_at": now_text,
                    "status": str(record.get("status") or "failed")[:80],
                    "detail": str(
                        record.get("bot_failure_detail")
                        or record.get("detail")
                        or ""
                    )[:300],
                },
            )
            failure_records[event_key] = {"notified_at": now_text}
            completed_count += 1
            failure_completed += 1
        except Exception as exc:
            failed_count += 1
            print(
                "WARNING auto participation owner failure sync: "
                f"wheel={key} {type(exc).__name__}: {exc}"
            )

    panel.current_chat_id, panel.current_user_id, panel.current_role = original_context
    return {
        "pending": len(success_candidates) + len(failure_candidates),
        "completed": completed_count,
        "failed": failed_count,
        "success_completed": success_completed,
        "failure_completed": failure_completed,
    }


def _sync_loop(panel: Any) -> None:
    # Let normal run() load access/setup Telegram and start snapshot refresh first.
    if panel.stop_refresh.wait(5):
        return
    while not panel.stop_refresh.is_set():
        try:
            result = sync_once(panel)
            if result.get("completed"):
                print(
                    "Auto participation authoritative owner sync completed: "
                    f"success={result['success_completed']} "
                    f"failure={result['failure_completed']}"
                )
        except Exception as exc:
            print(
                "WARNING auto participation authoritative owner sync loop: "
                f"{type(exc).__name__}: {exc}"
            )
        if panel.stop_refresh.wait(SYNC_INTERVAL_SECONDS):
            return


def install(panel_class: type[Any]) -> None:
    if getattr(panel_class, "_bbvg_auto_participation_owner_sync_installed", False):
        return
    original_run = panel_class.run

    def run_with_auto_participation_owner_sync(self: Any) -> int:
        thread = threading.Thread(
            target=_sync_loop,
            args=(self,),
            name="auto-participation-owner-sync",
            daemon=True,
        )
        thread.start()
        return original_run(self)

    panel_class.run = run_with_auto_participation_owner_sync
    panel_class._bbvg_auto_participation_owner_sync_installed = True


def self_test() -> None:
    state = {
        "auto_participation_events": {
            "lent#action:952:2026-07-21T14:01:28.861000+00:00": {
                "wheel_key": "lent",
                "status": "participated",
                "bot_success_pending_at": "2026-07-21T14:02:36+00:00",
            },
            "old#action:1:": {
                "wheel_key": "old",
                "status": "participated",
            },
            "failed#action:2:2026-07-21T14:00:00+00:00": {
                "wheel_key": "failed",
                "status": "unconfirmed",
                "bot_failure_pending_at": "2026-07-21T14:00:00+00:00",
            },
            "legacy#action:3:2026-07-21T14:00:00+00:00": {
                "wheel_key": "legacy",
                "status": "unconfirmed",
                "bot_failure_pending_at": "2026-07-21T14:00:00+00:00",
                "manual_notification_sent": True,
            },
            "race#action:4:2026-07-21T14:00:00+00:00": {
                "wheel_key": "race",
                "status": "unconfirmed",
                "bot_failure_pending_at": "2026-07-21T14:00:00+00:00",
                "bot_success_pending_at": "2026-07-21T14:01:00+00:00",
            },
        }
    }
    values = pending_events(state)
    assert [token for token, _ in values] == [
        "lent#action:952:2026-07-21T14:01:28.861000+00:00"
    ]
    failure_values = pending_failure_events(
        state,
        now=datetime(2026, 7, 21, 14, 5, tzinfo=UTC),
    )
    assert [token for token, _ in failure_values] == [
        "failed#action:2:2026-07-21T14:00:00+00:00"
    ]
    assert _event_token(
        {
            "action_id": 698,
            "server_start_at": "2026-07-22T08:04:56.849000+00:00",
        },
        "zonertg13",
    ) == "zonertg13#action:698:2026-07-22T08:04:56.849000+00:00"
    assert _event_token(
        {
            "wheel_key": "ctom11",
            "action_id": 958,
            "server_start_at": "2026-07-21T15:28:57.035000+00:00",
        }
    ) == "ctom11#action:958:2026-07-21T15:28:57.035000+00:00"
    print("auto participation authoritative owner sync self-test passed")


if __name__ == "__main__":
    self_test()
