from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
SOURCE_REQUEST_PERSONAL_RETENTION_DAYS = 90


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _prune_expiring_map(value: Any, current: datetime) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, raw in value.items():
        if not str(key):
            continue
        entry = raw if isinstance(raw, dict) else {}
        expires = parse_datetime(entry.get("expires_at"))
        if expires is not None and expires <= current:
            continue
        result[str(key).casefold()] = dict(entry)
    return result


def prune_bundle(bundle: dict[str, Any], *, current: datetime | None = None) -> bool:
    """Remove expired ephemeral data and anonymize old completed source requests."""

    current = current or datetime.now(UTC)
    changed = False
    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    for user_id, raw in list(users.items()):
        if not isinstance(raw, dict):
            users.pop(user_id, None)
            changed = True
            continue
        for field in ("participating_wheels", "hidden_wheels"):
            before = raw.get(field)
            after = _prune_expiring_map(before, current)
            if before != after:
                raw[field] = after
                changed = True

    requests_state = (
        bundle.get("source_requests")
        if isinstance(bundle.get("source_requests"), dict)
        else {}
    )
    requests = (
        requests_state.get("requests")
        if isinstance(requests_state.get("requests"), dict)
        else {}
    )
    cutoff = current - timedelta(days=SOURCE_REQUEST_PERSONAL_RETENTION_DAYS)
    personal_fields = (
        "requester_id",
        "requester_chat_id",
        "requester_name",
        "requester_username",
    )
    for raw in requests.values():
        if not isinstance(raw, dict) or str(raw.get("status") or "") == "pending":
            continue
        decided = parse_datetime(raw.get("decided_at")) or parse_datetime(raw.get("created_at"))
        if decided is None or decided > cutoff:
            continue
        if any(raw.get(field) for field in personal_fields):
            for field in personal_fields:
                raw[field] = ""
            raw["requester_anonymized_at"] = current.isoformat()
            changed = True
    return changed


def delete_user_data(
    bundle: dict[str, Any],
    user_id: str,
    *,
    current: datetime | None = None,
) -> bool:
    """Delete one Telegram user's personal state while preserving anonymous audit facts."""

    target = str(user_id or "")
    if not target:
        return False
    current = current or datetime.now(UTC)
    access = bundle.get("access") if isinstance(bundle.get("access"), dict) else {}
    if str(access.get("owner_id") or "") == target:
        raise PermissionError("Владелец не может удалить свои данные до передачи владения")

    users = access.get("users") if isinstance(access.get("users"), dict) else {}
    record = users.pop(target, None)
    chat_id = str(record.get("chat_id") or target) if isinstance(record, dict) else target
    changed = record is not None

    admins = [str(value) for value in access.get("admins", []) if str(value) != target]
    if admins != access.get("admins", []):
        access["admins"] = admins
        changed = True
    blocked = [str(value) for value in access.get("blocked_users", []) if str(value) != target]
    if blocked != access.get("blocked_users", []):
        access["blocked_users"] = blocked
        changed = True
    recipients = [
        str(value)
        for value in access.get("notification_recipients", [])
        if str(value) not in {target, chat_id}
    ]
    if recipients != access.get("notification_recipients", []):
        access["notification_recipients"] = recipients
        changed = True

    requests_state = (
        bundle.get("source_requests")
        if isinstance(bundle.get("source_requests"), dict)
        else {}
    )
    requests = (
        requests_state.get("requests")
        if isinstance(requests_state.get("requests"), dict)
        else {}
    )
    for request_id, raw in list(requests.items()):
        if not isinstance(raw, dict) or str(raw.get("requester_id") or "") != target:
            continue
        if str(raw.get("status") or "") == "pending":
            requests.pop(request_id, None)
        else:
            for field in (
                "requester_id",
                "requester_chat_id",
                "requester_name",
                "requester_username",
            ):
                raw[field] = ""
            raw["requester_deleted_at"] = current.isoformat()
        changed = True
    return changed


def self_test() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    bundle = {
        "access": {
            "owner_id": "1",
            "admins": ["2"],
            "blocked_users": [],
            "notification_recipients": ["10", "20"],
            "users": {
                "1": {"chat_id": "10"},
                "2": {
                    "chat_id": "20",
                    "hidden_wheels": {
                        "old": {"expires_at": "2026-07-14T00:00:00+00:00"},
                        "new": {"expires_at": "2026-07-16T00:00:00+00:00"},
                    },
                },
            },
        },
        "source_requests": {
            "requests": {
                "old": {
                    "status": "accepted",
                    "decided_at": "2026-01-01T00:00:00+00:00",
                    "requester_id": "2",
                    "requester_chat_id": "20",
                    "requester_name": "User",
                }
            }
        },
    }
    assert prune_bundle(bundle, current=now)
    assert "old" not in bundle["access"]["users"]["2"]["hidden_wheels"]
    assert bundle["source_requests"]["requests"]["old"]["requester_id"] == ""
    assert delete_user_data(bundle, "2", current=now)
    assert "2" not in bundle["access"]["users"]
    try:
        delete_user_data(bundle, "1", current=now)
    except PermissionError:
        pass
    else:
        raise AssertionError("Owner deletion must be rejected")
    print("privacy retention self-test passed")


if __name__ == "__main__":
    self_test()
