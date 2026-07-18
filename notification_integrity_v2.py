from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "notification_delivery_state.json"
FORMAT_V2 = "bbvg-notification-delivery-v2"
FORMAT = "bbvg-notification-delivery-v3"
UTC = timezone.utc
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
RETENTION_SECONDS = max(300, int(os.getenv("NOTIFICATION_DEDUP_SECONDS", "86400")))
MAX_ENTRIES = max(1000, int(os.getenv("NOTIFICATION_DEDUP_MAX_ENTRIES", "20000")))
CLAIM_TTL_SECONDS = max(30, int(os.getenv("NOTIFICATION_CLAIM_TTL_SECONDS", "300")))
MAX_CLAIMS = max(100, int(os.getenv("NOTIFICATION_CLAIM_MAX_ENTRIES", "2000")))

_lock = threading.RLock()
_volatile_entries: dict[str, datetime] = {}
_pending_entries: set[str] = set()


class NotificationIntegrityError(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC)


def _secret(explicit: str | None = None) -> bytes:
    raw = str(explicit or os.getenv("BOT_STATE_KEY") or "").strip()
    if not raw:
        raise NotificationIntegrityError(
            "BOT_STATE_KEY is required for persistent notification deduplication"
        )
    return raw.encode("utf-8")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def default_state() -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": 3,
        "algorithm": "HMAC-SHA256",
        "retention_seconds": RETENTION_SECONDS,
        "entries": {},
        "claims": {},
    }


def _state_path(path: Path | None = None) -> Path:
    return Path(path) if path is not None else STATE_PATH


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = _state_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_state()
    except (json.JSONDecodeError, OSError) as exc:
        raise NotificationIntegrityError(
            f"Unable to read notification delivery state: {type(exc).__name__}"
        ) from exc
    if not isinstance(raw, dict) or str(raw.get("format") or "") not in {
        FORMAT_V2,
        FORMAT,
    }:
        raise NotificationIntegrityError("Unsupported notification delivery state format")
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        raise NotificationIntegrityError("Notification delivery entries must be an object")
    normalized: dict[str, str] = {}
    for digest, delivered_at in entries.items():
        key = str(digest).casefold()
        timestamp = _parse_datetime(delivered_at)
        if HEX_DIGEST_RE.fullmatch(key) and timestamp is not None:
            normalized[key] = timestamp.isoformat()
    claims = raw.get("claims") if isinstance(raw.get("claims"), dict) else {}
    return {
        "format": FORMAT,
        "version": 3,
        "algorithm": "HMAC-SHA256",
        "retention_seconds": RETENTION_SECONDS,
        "entries": normalized,
        "claims": _pruned_claims(claims),
    }


def _pruned_entries(entries: dict[str, Any], current: datetime | None = None) -> dict[str, str]:
    current = current or now_utc()
    threshold = current - timedelta(seconds=RETENTION_SECONDS)
    rows: list[tuple[str, datetime]] = []
    for digest, delivered_at in entries.items():
        key = str(digest).casefold()
        timestamp = _parse_datetime(delivered_at)
        if HEX_DIGEST_RE.fullmatch(key) and timestamp is not None and timestamp >= threshold:
            rows.append((key, timestamp))
    rows.sort(key=lambda item: item[1], reverse=True)
    return {digest: timestamp.isoformat() for digest, timestamp in rows[:MAX_ENTRIES]}


def _pruned_claims(claims: dict[str, Any], current: datetime | None = None) -> dict[str, str]:
    current = current or now_utc()
    threshold = current - timedelta(seconds=CLAIM_TTL_SECONDS)
    rows: list[tuple[str, datetime]] = []
    for digest, claimed_at in claims.items():
        key = str(digest).casefold()
        timestamp = _parse_datetime(claimed_at)
        if HEX_DIGEST_RE.fullmatch(key) and timestamp is not None and timestamp >= threshold:
            rows.append((key, timestamp))
    rows.sort(key=lambda item: item[1], reverse=True)
    return {digest: timestamp.isoformat() for digest, timestamp in rows[:MAX_CLAIMS]}


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            import fcntl  # type: ignore[import-not-found]

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            yield
        finally:
            try:
                import fcntl  # type: ignore[import-not-found]

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


def save_state(value: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = _state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = value.get("entries") if isinstance(value, dict) else {}
    claims = value.get("claims") if isinstance(value, dict) else {}
    normalized = default_state()
    normalized["entries"] = _pruned_entries(entries if isinstance(entries, dict) else {})
    normalized["claims"] = _pruned_claims(claims if isinstance(claims, dict) else {})
    for digest in normalized["entries"]:
        normalized["claims"].pop(digest, None)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(
                json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return normalized


def delivery_digest(
    chat_id: str,
    kind: str,
    text: str,
    url: str | None,
    *,
    secret: str | None = None,
) -> str:
    payload = json.dumps(
        [str(chat_id), str(kind), str(text), str(url or "")],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_secret(secret), payload, hashlib.sha256).hexdigest()


def duplicate_delivery(digest: str, path: Path | None = None) -> bool:
    key = str(digest).casefold()
    if not HEX_DIGEST_RE.fullmatch(key):
        return False
    current = now_utc()
    with _lock:
        volatile = _volatile_entries.get(key)
        if volatile is not None and current - volatile <= timedelta(seconds=RETENTION_SECONDS):
            return True
        target = _state_path(path)
        with _file_lock(target):
            state = load_state(target)
        delivered_at = _parse_datetime(state["entries"].get(key))
        claimed_at = _parse_datetime(state["claims"].get(key))
        return bool(
            delivered_at is not None
            and current - delivered_at <= timedelta(seconds=RETENTION_SECONDS)
        ) or bool(
            claimed_at is not None
            and current - claimed_at <= timedelta(seconds=CLAIM_TTL_SECONDS)
        )


def remember_delivery(digest: str, path: Path | None = None) -> None:
    key = str(digest).casefold()
    if not HEX_DIGEST_RE.fullmatch(key):
        return
    current = now_utc()
    with _lock:
        target = _state_path(path)
        try:
            with _file_lock(target):
                state = load_state(target)
                state["claims"].pop(key, None)
                state["entries"][key] = current.isoformat()
                save_state(state, target)
        except Exception as exc:
            # Delivery has already succeeded at this point. Keep the volatile mark
            # and avoid turning a successful Telegram send into a retry storm.
            print(
                "WARNING notification delivery ledger was not persisted: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            _pending_entries.discard(key)
            _volatile_entries[key] = current


def claim_delivery(digest: str, path: Path | None = None) -> bool:
    """Reserve a delivery with an expiring interprocess lease."""

    key = str(digest).casefold()
    if not HEX_DIGEST_RE.fullmatch(key):
        return False
    current = now_utc()
    with _lock:
        if key in _pending_entries:
            return False
        volatile = _volatile_entries.get(key)
        if volatile is not None and current - volatile <= timedelta(
            seconds=RETENTION_SECONDS
        ):
            return False
        target = _state_path(path)
        with _file_lock(target):
            state = load_state(target)
            delivered_at = _parse_datetime(state["entries"].get(key))
            if delivered_at is not None and current - delivered_at <= timedelta(
                seconds=RETENTION_SECONDS
            ):
                return False
            claimed_at = _parse_datetime(state["claims"].get(key))
            if claimed_at is not None and current - claimed_at <= timedelta(
                seconds=CLAIM_TTL_SECONDS
            ):
                return False
            state["claims"][key] = current.isoformat()
            save_state(state, target)
        _pending_entries.add(key)
        return True


def release_delivery(digest: str, path: Path | None = None) -> None:
    """Allow retry after Telegram rejected or failed to send the message."""

    key = str(digest).casefold()
    if not HEX_DIGEST_RE.fullmatch(key):
        return
    with _lock:
        target = _state_path(path)
        with _file_lock(target):
            state = load_state(target)
            if key in state["claims"]:
                state["claims"].pop(key, None)
                save_state(state, target)
        _pending_entries.discard(key)


def complete_delivery(digest: str, path: Path | None = None) -> None:
    """Persist a successfully sent delivery and clear its reservation."""

    remember_delivery(digest, path)


def merge_states(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = default_state()
    entries: dict[str, Any] = {}
    claims: dict[str, Any] = {}
    for value in (left, right):
        raw = value.get("entries") if isinstance(value, dict) else None
        if isinstance(raw, dict):
            for digest, delivered_at in raw.items():
                key = str(digest).casefold()
                incoming = _parse_datetime(delivered_at)
                previous = _parse_datetime(entries.get(key))
                if HEX_DIGEST_RE.fullmatch(key) and incoming is not None and (
                    previous is None or incoming > previous
                ):
                    entries[key] = incoming.isoformat()
        raw_claims = value.get("claims") if isinstance(value, dict) else None
        if isinstance(raw_claims, dict):
            for digest, claimed_at in raw_claims.items():
                key = str(digest).casefold()
                incoming = _parse_datetime(claimed_at)
                previous = _parse_datetime(claims.get(key))
                if HEX_DIGEST_RE.fullmatch(key) and incoming is not None and (
                    previous is None or incoming > previous
                ):
                    claims[key] = incoming.isoformat()
    merged["entries"] = _pruned_entries(entries)
    merged["claims"] = _pruned_claims(claims)
    for digest in merged["entries"]:
        merged["claims"].pop(digest, None)
    return merged


def install(router_module: Any) -> None:
    if getattr(router_module, "_bbvg_notification_integrity_v2_installed", False):
        return

    original_kind: Callable[[str], str] = router_module.notification_kind
    original_recipients: Callable[[dict[str, Any], bool, str], list[str]] = router_module.recipients

    def strict_kind(text: str) -> str:
        lowered = router_module.html.unescape(str(text or "")).casefold()
        # A real wheel notification remains a user notification even when the
        # publication text happens to contain words such as "ошибка" or "сбой".
        if any(
            marker in lowered
            for marker in (
                "новое колесо betboom",
                "колесо betboom стало активно",
                "колесо betboom подтверждено администратором",
                "колесо betboom доступно для участия",
                "участие откроется позже",
                "активные колёса",
            )
        ):
            return "wheels"
        return original_kind(text)

    def strict_recipients(
        config: dict[str, Any], config_exists: bool, category: str
    ) -> list[str]:
        targets = original_recipients(config, config_exists, category)
        if not config_exists:
            return targets
        kind = {"admin": "admin_system", "user": "wheels"}.get(category, category)
        blocked = {str(value) for value in config.get("blocked_users", []) if str(value)}
        filtered: list[str] = []
        for chat_id in targets:
            user_id, _ = router_module.user_for_chat(config, str(chat_id))
            if user_id and user_id in blocked:
                continue
            if kind in router_module.ADMIN_NOTIFICATION_KINDS and not router_module.is_admin_chat(
                config, str(chat_id)
            ):
                continue
            filtered.append(str(chat_id))
        return sorted(set(filtered))

    router_module.notification_kind = strict_kind
    router_module.recipients = strict_recipients
    router_module.delivery_key = delivery_digest
    router_module.duplicate_delivery = duplicate_delivery
    router_module.remember_delivery = remember_delivery
    router_module.claim_delivery = claim_delivery
    router_module.release_delivery = release_delivery
    router_module.complete_delivery = complete_delivery
    router_module._bbvg_notification_integrity_v2_installed = True


def self_test() -> None:
    import notification_router

    original_path = STATE_PATH
    original_secret = os.environ.get("BOT_STATE_KEY")
    original_token = os.environ.get("BOT_TOKEN")
    try:
        with TemporaryDirectory() as temporary:
            globals()["STATE_PATH"] = Path(temporary) / "notification_delivery_state.json"
            os.environ.pop("BOT_STATE_KEY", None)
            os.environ["BOT_TOKEN"] = "must-not-be-used-as-state-key"
            try:
                _secret()
            except NotificationIntegrityError:
                pass
            else:
                raise AssertionError("BOT_TOKEN must never replace BOT_STATE_KEY")
            os.environ["BOT_STATE_KEY"] = "chapter-2-test-key"
            install(notification_router)

            digest = notification_router.delivery_key(
                "123456", "wheels", "Новое колесо BetBoom", "https://example.invalid/wheel"
            )
            assert HEX_DIGEST_RE.fullmatch(digest)
            assert not notification_router.duplicate_delivery(digest)
            assert notification_router.claim_delivery(digest)
            assert not notification_router.claim_delivery(digest)
            notification_router.complete_delivery(digest)
            assert notification_router.duplicate_delivery(digest)

            raw = STATE_PATH.read_text(encoding="utf-8")
            assert "123456" not in raw
            assert "Новое колесо" not in raw
            assert "example.invalid" not in raw
            state = load_state()
            assert digest in state["entries"]

            config = {
                "owner_id": "1",
                "admins": ["2"],
                "blocked_users": ["2"],
                "settings": {"notifications": True},
                "users": {
                    "1": {"chat_id": "10", "notifications_enabled": True},
                    "2": {"chat_id": "20", "notifications_enabled": True},
                    "3": {"chat_id": "30", "notifications_enabled": True},
                },
                "notification_recipients": ["10", "20", "30"],
            }
            assert notification_router.recipients(config, True, "admin_system") == ["10"]
            assert notification_router.recipients(config, True, "wheels") == ["10", "30"]
            assert notification_router.notification_kind(
                "🎡 Новое колесо BetBoom — ошибка в тексте публикации"
            ) == "wheels"
    finally:
        globals()["STATE_PATH"] = original_path
        if original_secret is None:
            os.environ.pop("BOT_STATE_KEY", None)
        else:
            os.environ["BOT_STATE_KEY"] = original_secret
        if original_token is None:
            os.environ.pop("BOT_TOKEN", None)
        else:
            os.environ["BOT_TOKEN"] = original_token
    print("notification integrity v2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--merge-from", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.merge_from:
        current = load_state()
        incoming = load_state(args.merge_from)
        save_state(merge_states(current, incoming))
        return 0
    if args.prune:
        save_state(load_state())
        return 0
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
