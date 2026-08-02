from __future__ import annotations

import argparse
import base64
import copy
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import monitor
import requests
import wheel_publications_v2
from bbvg.storage import event_id_from_entry

UTC = timezone.utc
DEFAULT_RECOVERY_RESULT = Path("/tmp/bbvg-auto-participation-recovery.json")
PRIMARY_ACCOUNT_KEY = "vyacheslav_primary"
PRIMARY_ACCOUNT_LABEL = "Аккаунт 1"
SUCCESS_STATUSES = {
    "participated",
    "already_participating",
    "already_marked_participating",
}
GITHUB_API_VERSION = "2022-11-28"
RUNTIME_STATE_BRANCH = "runtime-state"
RUNTIME_PUBLISH_ATTEMPTS = 5
RUNTIME_PUBLISH_TIMEOUT_SECONDS = 20
MISSING_ACTIVE_UNTIMED_TTL = timedelta(hours=2)
EMERGENCY_NOTIFICATION_MARKER = Path(
    "/tmp/bbvg-auto-participation-emergency-notified"
)

_AUTO_PARTICIPATION_FIELDS = {
    "participating",
    "auto_participation_status",
    "auto_participation_checked_at",
    "auto_participation_confirmed_at",
    "auto_participation_retry_allowed",
    "auto_participation_error",
    "auto_participation_manual_notification_at",
    "auto_participation_manual_notification_error",
    "auto_participation_rearmed_at",
    "auto_participation_rearm_reason",
    "recovered_initial_notification_pending_at",
    "recovered_initial_notification_reason",
    "recovered_initial_notification_error",
    "recovered_initial_notification_sent_at",
    "recovered_initial_notification_duplicate_suppressed",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "BB-VG-auto-participation-state-sync",
    }


def publish_runtime_state(
    local_path: Path,
    *,
    token: str,
    repository: str,
    branch: str = RUNTIME_STATE_BRANCH,
    attempts: int = RUNTIME_PUBLISH_ATTEMPTS,
) -> dict[str, Any]:
    """CAS-merge auto-participation outcomes into Control Center state.

    Only the fields owned by ``merge_auto_participation_state`` are copied
    from the worker result.  Every conflict re-reads the current remote blob
    and repeats the semantic merge, so a monitor checkpoint cannot be erased.
    """

    local = _load_json(local_path, {})
    if not isinstance(local, dict):
        raise RuntimeError(f"invalid local state: {local_path}")
    if not token or not repository:
        raise RuntimeError("GitHub credentials are required for runtime-state publish")

    url = f"https://api.github.com/repos/{repository}/contents/state.json"
    headers = _github_headers(token)
    last_error = ""
    for attempt in range(1, max(1, attempts) + 1):
        response = requests.get(
            url,
            headers=headers,
            params={"ref": branch},
            timeout=RUNTIME_PUBLISH_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"runtime_state_read_http_{response.status_code}:{response.text[:300]}"
            )
        payload = response.json()
        sha = str(payload.get("sha") or "")
        try:
            remote = json.loads(
                base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"runtime_state_decode_failed:{type(exc).__name__}:{exc}"
            ) from exc
        merged = merge_auto_participation_state(remote, local)
        if merged == remote:
            return {
                "branch": branch,
                "attempt": attempt,
                "changed": False,
                "sha": sha,
            }
        encoded = base64.b64encode(
            (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii")
        update = requests.put(
            url,
            headers=headers,
            json={
                "message": "Publish auto participation outcome [skip ci]",
                "content": encoded,
                "branch": branch,
                "sha": sha,
            },
            timeout=RUNTIME_PUBLISH_TIMEOUT_SECONDS,
        )
        if update.status_code in {200, 201}:
            result = update.json()
            commit = result.get("commit") if isinstance(result, dict) else {}
            return {
                "branch": branch,
                "attempt": attempt,
                "changed": True,
                "sha": str(commit.get("sha") or ""),
            }
        last_error = f"http_{update.status_code}:{update.text[:300]}"
        if update.status_code not in {409, 422}:
            break
    raise RuntimeError(
        f"runtime_state_publish_failed_after_{attempts}_attempts:{last_error}"
    )


def _load_recovery_result(path: Path) -> dict[str, Any]:
    """Read the last JSON object emitted by auto_participation_recovery.py."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        value = line.strip()
        if not value:
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _event_token(item: dict[str, Any]) -> str:
    return event_id_from_entry(item)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _record_timestamp(record: dict[str, Any]) -> datetime:
    candidates: list[datetime] = []
    for field, value in record.items():
        if field.endswith("_at") or field in {"attempted_at", "recorded_at"}:
            parsed = _parse_datetime(value)
            if parsed is not None:
                candidates.append(parsed)
    return max(candidates, default=datetime.min.replace(tzinfo=UTC))


def _active_event_marker(record: Any) -> datetime:
    if not isinstance(record, dict):
        return datetime.min.replace(tzinfo=UTC)
    for field in ("server_start_at", "message_date", "first_notified_at", "created_at"):
        parsed = _parse_datetime(record.get(field))
        if parsed is not None:
            return parsed
    return _record_timestamp(record)


def _active_event_is_newer(remote: Any, local: Any) -> bool:
    if not isinstance(local, dict):
        return False
    if not isinstance(remote, dict):
        return True
    remote_token = _event_token(remote)
    local_token = _event_token(local)
    if remote_token == local_token:
        return False
    remote_marker = _active_event_marker(remote)
    local_marker = _active_event_marker(local)
    if local_marker != remote_marker:
        return local_marker > remote_marker
    return _record_timestamp(local) > _record_timestamp(remote)


def _worker_can_restore_missing_active(
    record: Any,
    *,
    current: datetime | None = None,
) -> bool:
    """Allow a worker to add lifecycle state only for a demonstrably live event.

    The Monitor owns deletion of closed wheels. A delayed browser worker may still
    publish durable account outcomes, but it must not recreate an active card after
    the Monitor removed that generation. A concrete deadline is authoritative; an
    expired deadline can never be extended by a synthetic two-hour expiry.
    """

    if not isinstance(record, dict):
        return False
    now = current or datetime.now(UTC)
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    deadline = _parse_datetime(record.get("deadline"))
    if deadline is not None:
        return deadline > now
    expires_at = _parse_datetime(record.get("expires_at"))
    if expires_at is None or expires_at <= now:
        return False
    event_at = _parse_datetime(record.get("server_start_at"))
    if event_at is None:
        event_at = _parse_datetime(record.get("message_date"))
    if event_at is None:
        return False
    return event_at >= now - MISSING_ACTIVE_UNTIMED_TTL


def _suppress_delivered_recovery_pending(record: Any) -> bool:
    """Do not resurrect a recovery notification after it was already delivered."""

    if not isinstance(record, dict):
        return False
    pending = _parse_datetime(record.get("recovered_initial_notification_pending_at"))
    if pending is None:
        return False
    delivered = [
        value
        for field in (
            "recovered_initial_notification_sent_at",
            "last_notification_at",
            "first_notified_at",
        )
        if (value := _parse_datetime(record.get(field))) is not None
        and value >= pending
    ]
    if not delivered:
        return False
    record.pop("recovered_initial_notification_pending_at", None)
    record.pop("recovered_initial_notification_reason", None)
    record.pop("recovered_initial_notification_error", None)
    record.setdefault(
        "recovered_initial_notification_sent_at",
        max(delivered).isoformat(),
    )
    record["recovered_initial_notification_duplicate_suppressed"] = True
    return True


def _event_context(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("wheel_key") or item.get("identifier") or "").casefold()
    source = dict(item)
    active = state.get("active_wheels")
    active_item = active.get(key) if isinstance(active, dict) else None
    if isinstance(active_item, dict) and _event_token(active_item) == _event_token(item):
        source = dict(active_item)
        source.update(item)
    fields = (
        "identifier",
        "url",
        "source",
        "message_id",
        "message_date",
        "message_url",
        "message_text",
        "button_token",
        "action_id",
        "server_start_at",
        "deadline",
        "available_at",
        "generation_id",
        "event_id",
        "wheel_type",
        "referral_suspected",
        "referral_classification_evidence",
    )
    context = {
        field: copy.deepcopy(source[field])
        for field in fields
        if field in source
    }
    context["wheel_key"] = key
    context.setdefault("identifier", key)
    context["referral_restricted"] = (
        wheel_publications_v2.entry_is_referral_restricted(source)
    )
    context["wheel_type"] = wheel_publications_v2.referral_classification(source)
    return context


def _record_success(record: Any) -> bool:
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
    return result


def _merge_record_collection(
    target: dict[str, Any],
    remote: Any,
    local: Any,
) -> None:
    remote_rows = remote if isinstance(remote, dict) else {}
    local_rows = local if isinstance(local, dict) else {}
    for key in set(remote_rows) | set(local_rows):
        if key in remote_rows and key in local_rows:
            target[str(key)] = _merge_timed_record(remote_rows[key], local_rows[key])
        elif key in local_rows:
            target[str(key)] = copy.deepcopy(local_rows[key])
        else:
            target[str(key)] = copy.deepcopy(remote_rows[key])


def merge_auto_participation_state(
    remote_state: dict[str, Any],
    local_state: dict[str, Any],
    *,
    current: datetime | None = None,
) -> dict[str, Any]:
    """Merge one workflow outcome into the latest monitor state.

    The monitor remains authoritative for lifecycle and source discovery. The isolated
    workflow owns only auto-participation outcome fields and its event/dispatch ledgers.
    This prevents a heartbeat or monitor commit from erasing a confirmed BetBoom result.
    """

    remote = remote_state if isinstance(remote_state, dict) else {}
    local = local_state if isinstance(local_state, dict) else {}
    merged = copy.deepcopy(remote)
    now = current or datetime.now(UTC)
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)

    for collection_name in (
        "auto_participation_events",
        "auto_participation_dispatch_events",
        "auto_participation_attempts",
    ):
        rows: dict[str, Any] = {}
        _merge_record_collection(
            rows,
            remote.get(collection_name),
            local.get(collection_name),
        )
        if rows:
            merged[collection_name] = rows

    remote_active = remote.get("active_wheels")
    local_active = local.get("active_wheels")
    active = copy.deepcopy(remote_active) if isinstance(remote_active, dict) else {}
    if isinstance(local_active, dict):
        for raw_key, raw_item in local_active.items():
            key = str(raw_key).casefold()
            if not isinstance(raw_item, dict):
                continue
            current_item = active.get(key)
            if not isinstance(current_item, dict):
                if _worker_can_restore_missing_active(raw_item, current=now):
                    active[key] = copy.deepcopy(raw_item)
                continue
            if _active_event_is_newer(current_item, raw_item):
                if not _worker_can_restore_missing_active(raw_item, current=now):
                    continue
                updated = copy.deepcopy(current_item)
                updated.update(copy.deepcopy(raw_item))
                active[key] = updated
                continue
            updated = copy.deepcopy(current_item)
            success_already_confirmed = bool(
                current_item.get("participating")
                or current_item.get("auto_participation_confirmed_at")
                or str(current_item.get("auto_participation_status") or "").casefold() in SUCCESS_STATUSES
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
    for item in active.values():
        _suppress_delivered_recovery_pending(item)
    if active:
        merged["active_wheels"] = active
    else:
        merged.pop("active_wheels", None)

    for collection_name in ("button_contexts", "participating_wheels"):
        remote_rows = remote.get(collection_name)
        local_rows = local.get(collection_name)
        rows = copy.deepcopy(remote_rows) if isinstance(remote_rows, dict) else {}
        if isinstance(local_rows, dict):
            for key, value in local_rows.items():
                normalized = str(key)
                if normalized not in rows:
                    rows[normalized] = copy.deepcopy(value)
                elif collection_name == "participating_wheels":
                    rows[normalized] = _merge_timed_record(rows[normalized], value)
        if rows:
            merged[collection_name] = rows

    remote_publications = remote.get("wheel_publications")
    local_publications = local.get("wheel_publications")
    publications = (
        copy.deepcopy(remote_publications)
        if isinstance(remote_publications, dict)
        else {}
    )
    if isinstance(local_publications, dict):
        for key, value in local_publications.items():
            if key not in publications:
                publications[key] = copy.deepcopy(value)
            elif isinstance(publications.get(key), dict) and isinstance(value, dict):
                combined = copy.deepcopy(publications[key])
                combined.update(copy.deepcopy(value))
                publications[key] = combined
    if publications:
        merged["wheel_publications"] = publications

    if "auto_participation_event_mode_initialized_at" in local:
        merged.setdefault(
            "auto_participation_event_mode_initialized_at",
            local["auto_participation_event_mode_initialized_at"],
        )
    return merged


def merge_state_files(
    local_path: Path,
    remote_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    local = _load_json(local_path, {})
    remote = _load_json(remote_path, {})
    merged = merge_auto_participation_state(remote, local)
    _write_json(output_path, merged)
    return merged


def queue_recovery_outcomes(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    """Queue recovery outcomes for the single live Control Center.

    Workflow/recovery owns only public state.json. It never writes encrypted user
    state and never sends the final success/failure Telegram outcome. Personal
    marking, rating and final user-facing outcome are serialized by Control Center.
    """

    recovery = _load_recovery_result(recovery_result_path)
    state = _load_json(monitor.STATE_PATH, {})
    events = state.setdefault("auto_participation_events", {})
    attempts = (
        recovery.get("attempts")
        if isinstance(recovery.get("attempts"), list)
        else []
    )
    success_queued: list[str] = []
    failure_queued: list[str] = []
    changed = False
    now_text = datetime.now(UTC).isoformat()

    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        token = _event_token(attempt)
        record = events.get(token)
        if not isinstance(record, dict):
            continue
        record.setdefault("account_key", PRIMARY_ACCOUNT_KEY)
        record.setdefault("account_label", PRIMARY_ACCOUNT_LABEL)
        record.setdefault("event_token", token)
        context = _event_context(state, attempt)
        if context and record.get("event_context") != context:
            record["event_context"] = context
            changed = True

        if bool(attempt.get("success")):
            for field in (
                "bot_failure_pending_at",
                "bot_failure_sync_status",
                "bot_failure_sync_version",
                "bot_failure_status",
                "bot_failure_detail",
            ):
                if field in record:
                    record.pop(field, None)
                    changed = True

            if str(attempt.get("status") or "") == "already_marked_participating":
                continue
            if str(record.get("status") or "") != "participated":
                continue
            if not record.get("bot_success_pending_at"):
                record["bot_success_pending_at"] = now_text
                record["bot_success_sync_status"] = "waiting_for_control_center"
                record["bot_success_sync_version"] = 1
                success_queued.append(token)
                changed = True
            continue

        if bool(record.get("manual_notification_sent")):
            continue
        if record.get("bot_success_pending_at"):
            continue
        if str(record.get("status") or "") in {
            "participated",
            "already_marked_participating",
        }:
            continue
        if not record.get("bot_failure_pending_at"):
            record["bot_failure_pending_at"] = now_text
            failure_queued.append(token)
            changed = True
        record["bot_failure_sync_status"] = "waiting_for_control_center"
        record["bot_failure_sync_version"] = 1
        record["bot_failure_status"] = str(attempt.get("status") or "failed")[:80]
        record["bot_failure_detail"] = str(
            attempt.get("detail") or "автоучастие не подтверждено"
        )[:300]
        changed = True

    if changed:
        monitor.save_state(state)
    return {
        "success_queued": len(success_queued),
        "failure_queued": len(failure_queued),
        "success_events": success_queued,
        "failure_events": failure_queued,
    }


def queue_confirmed_participation(
    recovery_result_path: Path = DEFAULT_RECOVERY_RESULT,
) -> dict[str, Any]:
    """Backward-compatible entrypoint; outcomes are now finalized by Control Center."""

    return queue_recovery_outcomes(recovery_result_path)


def _record_event_id(token: str, record: dict[str, Any]) -> str:
    for value in (
        record.get("canonical_event_id"),
        record.get("event_id"),
        record.get("event_token"),
        token,
    ):
        base = str(value or "").split("#account:", 1)[0].strip()
        if base.startswith("evt:"):
            return base
    context = record.get("event_context")
    if isinstance(context, dict):
        return event_id_from_entry(context)
    return ""


def emergency_notify_event(
    state_path: Path,
    raw_event_payload: str,
    *,
    marker_path: Path = EMERGENCY_NOTIFICATION_MARKER,
) -> dict[str, Any]:
    """Deliver an owner-scoped fallback when runtime-state cannot be published.

    GitHub is only the normal hand-off transport between the ephemeral browser
    worker and Control Center. If that transport is rate-limited after the
    browser results already exist, do not silently discard the user outcome.
    This fallback is deliberately restricted to the configured admin chat and
    to Vyacheslav's accounts; accounts owned by another user are never mixed
    into the message.
    """

    if marker_path.exists():
        return {"status": "duplicate_suppressed", "sent": False}
    try:
        payload = json.loads(str(raw_event_payload or ""))
    except json.JSONDecodeError:
        return {"status": "invalid_event_payload", "sent": False}
    if not isinstance(payload, dict):
        return {"status": "invalid_event_payload", "sent": False}
    event_id = str(
        payload.get("event_id") or event_id_from_entry(payload)
    ).strip()
    state = _load_json(state_path, {})
    events = state.get("auto_participation_events")
    if not event_id or not isinstance(events, dict):
        return {"status": "event_results_missing", "sent": False}

    owner_results: dict[str, dict[str, Any]] = {}
    for token, raw in events.items():
        if not isinstance(raw, dict) or _record_event_id(str(token), raw) != event_id:
            continue
        account_key = str(raw.get("account_key") or "").strip()
        owner = str(raw.get("account_owner") or "").strip()
        if owner and owner != "vyacheslav":
            continue
        if account_key not in {"vyacheslav_primary", "vyacheslav_secondary"}:
            continue
        owner_results[account_key] = raw
    expected = {"vyacheslav_primary", "vyacheslav_secondary"}
    if not expected.issubset(owner_results):
        return {
            "status": "account_results_incomplete",
            "sent": False,
            "accounts": sorted(owner_results),
        }

    identifier = str(
        payload.get("identifier") or payload.get("wheel_key") or "wheel"
    ).strip()
    lines = [
        "⚠️ <b>Резервный итог автоучастия</b>",
        "",
        f"Колесо: <code>{html.escape(identifier)}</code>",
        "GitHub временно не принял checkpoint, поэтому итог отправлен напрямую:",
    ]
    for key in ("vyacheslav_primary", "vyacheslav_secondary"):
        record = owner_results[key]
        label = html.escape(str(record.get("account_label") or key))
        status = str(record.get("status") or "unknown").casefold()
        if status == "participated":
            outcome = "✅ участие принято автоматически"
        elif status in {"already_participating", "already_marked_participating"}:
            outcome = "✅ участие уже было принято и подтверждено BetBoom"
        else:
            detail = html.escape(
                str(record.get("detail") or record.get("error_text") or status)[:160]
            )
            outcome = f"❌ участие не подтверждено: {detail}"
        lines.append(f"• {label}: {outcome}")
    lines.extend(("", f"Event ID: <code>{html.escape(event_id)}</code>"))
    request: dict[str, Any] = {
        "chat_id": os.environ["BOT_CHAT_ID"],
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    url = str(payload.get("url") or "").strip()
    if url:
        request["reply_markup"] = {
            "inline_keyboard": [[{"text": "🎡 Открыть колесо", "url": url}]]
        }
    response = monitor.telegram_api("sendMessage", request)
    message_id = (
        response.get("result", {}).get("message_id")
        if isinstance(response, dict)
        and isinstance(response.get("result"), dict)
        else None
    )
    marker_path.write_text(
        json.dumps(
            {
                "event_id": event_id,
                "message_id": message_id,
                "sent_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "status": "sent",
        "sent": True,
        "event_id": event_id,
        "message_id": message_id,
    }


def self_test() -> None:
    success = {
        "wheel_key": "lent",
        "action_id": 952,
        "server_start_at": "2026-07-21T14:01:28.861000+00:00",
        "success": True,
        "status": "participated",
    }
    failure = {
        "wheel_key": "ctom11",
        "action_id": 958,
        "server_start_at": "2026-07-21T15:28:57.035000+00:00",
        "success": False,
        "status": "unconfirmed",
    }
    assert _event_token(success) == "evt:8677da38477b0f44018a"
    assert _event_token(failure) == "evt:6a1e2e7b3df94976b1a9"
    assert (
        _event_token({"wheel_key": "x", "message_date": "now"})
        == "pending:035ba460a92f92976ab4"
    )

    recurring_remote = {
        "active_wheels": {
            "zonertw5": {
                "wheel_key": "zonertw5",
                "action_id": 961,
                "server_start_at": "2026-07-22T16:27:00+00:00",
                "last_checked_at": "2026-07-22T18:30:00+00:00",
            }
        }
    }
    recurring_local = {
        "active_wheels": {
            "zonertw5": {
                "wheel_key": "zonertw5",
                "action_id": 989,
                "server_start_at": "2026-07-22T18:26:05+00:00",
                "message_date": "2026-07-22T18:27:00+00:00",
                "deadline": "2026-07-22T19:30:00+00:00",
                "participating": True,
            }
        }
    }
    recurring_merged = merge_auto_participation_state(
        recurring_remote,
        recurring_local,
        current=datetime(2026, 7, 22, 18, 40, tzinfo=UTC),
    )
    assert recurring_merged["active_wheels"]["zonertw5"]["action_id"] == 989
    assert recurring_merged["active_wheels"]["zonertw5"]["participating"] is True

    stale_missing = {
        "active_wheels": {
            "over": {
                "wheel_key": "over",
                "action_id": 1052,
                "server_start_at": "2026-07-26T11:55:26.955000+00:00",
                "deadline": "2026-07-26T12:15:26.955000+00:00",
                "expires_at": "2026-07-26T18:43:28.809501+00:00",
                "participating": True,
            }
        },
        "auto_participation_events": {
            "evt:stale": {
                "status": "participated",
                "attempted_at": "2026-07-26T12:09:00+00:00",
            }
        },
    }
    stale_merged = merge_auto_participation_state(
        {"active_wheels": {}},
        stale_missing,
        current=datetime(2026, 7, 26, 16, 45, tzinfo=UTC),
    )
    assert "over" not in stale_merged.get("active_wheels", {})
    assert stale_merged["auto_participation_events"]["evt:stale"]["status"] == "participated"

    delivered_remote = {
        "active_wheels": {
            "zonertg14": {
                "wheel_key": "zonertg14",
                "action_id": 699,
                "server_start_at": "2026-07-23T09:11:39.433000+00:00",
                "first_notified_at": "2026-07-23T13:22:09.380714+00:00",
                "last_notification_at": "2026-07-23T14:28:42.904375+00:00",
            }
        }
    }
    stale_local = {
        "active_wheels": {
            "zonertg14": {
                "wheel_key": "zonertg14",
                "action_id": 699,
                "server_start_at": "2026-07-23T09:11:39.433000+00:00",
                "recovered_initial_notification_pending_at": (
                    "2026-07-23T11:59:16.844122+00:00"
                ),
                "recovered_initial_notification_reason": (
                    "recovery_discovered_missing_event"
                ),
                "auto_participation_checked_at": (
                    "2026-07-23T14:49:27.088931+00:00"
                ),
            }
        }
    }
    delivered_merged = merge_auto_participation_state(
        delivered_remote,
        stale_local,
    )
    delivered_item = delivered_merged["active_wheels"]["zonertg14"]
    assert "recovered_initial_notification_pending_at" not in delivered_item
    assert delivered_item["recovered_initial_notification_duplicate_suppressed"] is True

    remote = {
        "version": 6,
        "active_wheels": {
            "wheel": {
                "wheel_key": "wheel",
                "last_checked_at": "new-monitor-value",
                "participating": False,
            }
        },
        "auto_participation_events": {
            "wheel#action:1:start": {
                "wheel_key": "wheel",
                "status": "queued",
                "recorded_at": "2026-07-22T08:00:00+00:00",
                "remote_field": True,
            }
        },
    }
    local = {
        "active_wheels": {
            "wheel": {
                "wheel_key": "wheel",
                "last_checked_at": "stale-worker-value",
                "participating": True,
                "auto_participation_status": "participated",
            }
        },
        "auto_participation_events": {
            "wheel#action:1:start": {
                "wheel_key": "wheel",
                "status": "participated",
                "attempted_at": "2026-07-22T08:01:00+00:00",
                "bot_success_pending_at": "2026-07-22T08:01:01+00:00",
            }
        },
    }
    merged = merge_auto_participation_state(remote, local)
    item = merged["active_wheels"]["wheel"]
    assert item["last_checked_at"] == "new-monitor-value"
    assert item["participating"] is True
    assert item["auto_participation_status"] == "participated"
    event = merged["auto_participation_events"]["wheel#action:1:start"]
    assert event["status"] == "participated"
    assert event["remote_field"] is True
    assert event["bot_success_pending_at"]
    print("auto participation bot outcome sync self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-result", default=str(DEFAULT_RECOVERY_RESULT))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--merge-local", type=Path)
    parser.add_argument("--merge-remote", type=Path)
    parser.add_argument("--merge-output", type=Path)
    parser.add_argument("--publish-runtime-state", type=Path)
    parser.add_argument("--emergency-notify-event", type=Path)
    parser.add_argument(
        "--emergency-notification-marker",
        type=Path,
        default=EMERGENCY_NOTIFICATION_MARKER,
    )
    parser.add_argument(
        "--runtime-state-branch",
        default=os.getenv("BBVG_RUNTIME_STATE_BRANCH", RUNTIME_STATE_BRANCH),
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    merge_args = (args.merge_local, args.merge_remote, args.merge_output)
    if any(merge_args):
        if not all(merge_args):
            parser.error(
                "--merge-local, --merge-remote and --merge-output are required together"
            )
        merge_state_files(args.merge_local, args.merge_remote, args.merge_output)
        print(
            json.dumps(
                {"merged": True, "output": str(args.merge_output)},
                ensure_ascii=False,
            )
        )
        return 0
    if args.publish_runtime_state:
        result = publish_runtime_state(
            args.publish_runtime_state,
            token=os.getenv("GH_TOKEN", "").strip()
            or os.getenv("GITHUB_TOKEN", "").strip(),
            repository=os.getenv("GITHUB_REPOSITORY", "").strip(),
            branch=str(args.runtime_state_branch or RUNTIME_STATE_BRANCH),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.emergency_notify_event:
        result = emergency_notify_event(
            args.emergency_notify_event,
            os.getenv("BBVG_EVENT_PAYLOAD_JSON", ""),
            marker_path=args.emergency_notification_marker,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if bool(result.get("sent")) else 1
    result = queue_recovery_outcomes(Path(args.recovery_result))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
