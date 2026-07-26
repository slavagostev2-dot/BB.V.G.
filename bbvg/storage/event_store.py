from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


UTC = timezone.utc
SCHEMA_VERSION = 2
DEFAULT_BUSY_TIMEOUT_MS = 15_000

SUCCESS_STATUSES = {
    "participated",
    "already_participated",
    "already_participating",
    "already_marked_participating",
}
TERMINAL_FAILURE_STATUSES = {
    "button_not_found",
    "participation_unavailable",
    "wheel_closed",
    "inactive",
}
TRANSIENT_FAILURE_STATUSES = {
    "browser_error",
    "dispatch_error",
    "network_error",
    "timeout",
    "transport_error",
    "unconfirmed",
    "unknown",
    "workflow_dispatch_failed",
    "workflow_dispatch_timeout",
    "already_marked_in_bot",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    value = value or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def canonical_start_at(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return ""
    else:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def canonical_generation_id(
    wheel_key: Any,
    action_id: Any,
    server_start_at: Any,
) -> str:
    key = str(wheel_key or "").strip().casefold()
    try:
        action = int(action_id or 0)
    except (TypeError, ValueError):
        action = 0
    start = canonical_start_at(server_start_at)
    if not key or action <= 0 or not start:
        return ""
    raw = "\x1f".join((key, str(action), start))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def canonical_event_id(
    wheel_key: Any,
    action_id: Any,
    server_start_at: Any,
) -> str:
    generation = canonical_generation_id(wheel_key, action_id, server_start_at)
    return f"evt:{generation}" if generation else ""


def provisional_event_id(
    wheel_key: Any,
    source: Any,
    message_id: Any,
    message_date: Any,
    action_id: Any = None,
) -> str:
    key = str(wheel_key or "").strip().casefold()
    source_key = str(source or "").strip().lstrip("@").casefold()
    try:
        post_id = int(message_id or 0)
    except (TypeError, ValueError):
        post_id = 0
    action = _safe_int(action_id) or 0
    source_date = canonical_start_at(message_date)
    if not key:
        return ""
    parts = [key, source_key, str(post_id), source_date]
    if action:
        parts.append(str(action))
    raw = "\x1f".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"pending:{digest}"


def event_id_from_entry(
    entry: dict[str, Any],
    *,
    wheel_key: Any = "",
) -> str:
    """Return the one canonical identity used by every runtime component."""

    key = wheel_key or entry.get("wheel_key") or entry.get("identifier")
    canonical = canonical_event_id(
        key,
        entry.get("action_id"),
        entry.get("server_start_at"),
    )
    if canonical:
        return canonical
    return provisional_event_id(
        key,
        entry.get("source"),
        entry.get("message_id"),
        entry.get("message_date"),
        entry.get("action_id"),
    )


def legacy_event_aliases(
    entry: dict[str, Any],
    *,
    wheel_key: Any = "",
) -> set[str]:
    """Enumerate historical identities for migration, never for new writes."""

    key = str(
        wheel_key or entry.get("wheel_key") or entry.get("identifier") or ""
    ).strip().casefold()
    if not key:
        return set()
    result: set[str] = set()
    action = _safe_int(entry.get("action_id"))
    raw_start = str(entry.get("server_start_at") or "").strip()
    if action:
        result.add(f"{key}#action:{action}:{raw_start}")
        normalized_start = canonical_start_at(raw_start)
        if normalized_start:
            result.add(f"{key}#action:{action}:{normalized_start}")
    for field in ("event_id", "generation_id"):
        value = str(entry.get(field) or "").strip()
        if value:
            result.add(f"{key}#event:{value}")
    first_seen = str(
        entry.get("first_notified_at")
        or entry.get("message_date")
        or entry.get("created_at")
        or ""
    ).strip()
    if first_seen:
        result.add(f"{key}#seen:{first_seen}")
    return result


def status_confidence(status: Any, confirmation: Any = "") -> int:
    normalized = str(status or "").strip().casefold()
    proof = str(confirmation or "").strip().casefold()
    if normalized in SUCCESS_STATUSES:
        if any(marker in proof for marker in ("exact", "api", "text", "confirmed")):
            return 120
        return 110
    if normalized in TERMINAL_FAILURE_STATUSES:
        return 70 if any(marker in proof for marker in ("api", "exact")) else 55
    if normalized in TRANSIENT_FAILURE_STATUSES:
        return 20
    if normalized in {"scheduled", "started", "running", "pending"}:
        return 10
    return 0


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class EventStore:
    """Transactional local event store with append-only audit history.

    SQLite/WAL is authoritative inside a production process. GitHub
    synchronization is deliberately outside every transaction and may retry in
    the background without blocking discovery, browser work or Telegram.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        audit_path: str | Path | None = None,
        notification_audit_path: str | Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        runtime_root = Path(
            os.getenv("BBVG_RUNTIME_DIR", str(root / "runtime"))
        )
        self.path = Path(
            path
            or os.getenv("BBVG_EVENT_DB_PATH", str(runtime_root / "bbvg_events.sqlite3"))
        )
        self.audit_path = Path(
            audit_path
            or os.getenv(
                "BBVG_EVENT_AUDIT_PATH",
                str(runtime_root / "event_ledger.jsonl"),
            )
        )
        self.notification_audit_path = Path(
            notification_audit_path
            or os.getenv(
                "BBVG_NOTIFICATION_AUDIT_PATH",
                str(runtime_root / "notification_audit_ledger.jsonl"),
            )
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.notification_audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._thread_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._thread_lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    wheel_key TEXT NOT NULL,
                    action_id INTEGER,
                    server_start_at TEXT,
                    source TEXT,
                    source_message_id INTEGER,
                    source_message_date TEXT,
                    source_message_url TEXT,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    referral_restricted INTEGER NOT NULL DEFAULT 0,
                    provisional INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL,
                    api_confirmed_at TEXT,
                    persisted_at TEXT NOT NULL,
                    dispatch_queued_at TEXT,
                    workflow_started_at TEXT,
                    browser_started_at TEXT,
                    notification_sent_at TEXT,
                    closed_at TEXT,
                    final_sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS events_day_idx
                ON events(server_start_at, discovered_at);

                CREATE UNIQUE INDEX IF NOT EXISTS events_generation_idx
                ON events(generation_id)
                WHERE generation_id <> '';

                CREATE TABLE IF NOT EXISTS event_aliases (
                    alias TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id)
                        ON UPDATE CASCADE ON DELETE CASCADE,
                    migrated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_transitions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    transition_id TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL REFERENCES events(event_id)
                        ON UPDATE CASCADE ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS transition_dedupe_idx
                ON event_transitions(event_id, stage, dedupe_key);

                CREATE TABLE IF NOT EXISTS outbox (
                    outbox_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id)
                        ON UPDATE CASCADE ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    claimed_at TEXT,
                    claim_token TEXT,
                    claim_expires_at TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(event_id, kind, scope)
                );

                CREATE INDEX IF NOT EXISTS outbox_pending_idx
                ON outbox(status, available_at);

                CREATE TABLE IF NOT EXISTS account_results (
                    event_id TEXT NOT NULL REFERENCES events(event_id)
                        ON UPDATE CASCADE ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    account_key TEXT NOT NULL,
                    account_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confirmation TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_text TEXT,
                    attempt_count INTEGER NOT NULL,
                    artifact_url TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, owner_id, account_key)
                );

                CREATE TABLE IF NOT EXISTS account_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id)
                        ON UPDATE CASCADE ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    account_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confirmation TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_text TEXT,
                    artifact_url TEXT,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event_id TEXT REFERENCES events(event_id)
                        ON UPDATE CASCADE ON DELETE SET NULL,
                    notification_type TEXT NOT NULL,
                    recipient_scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    telegram_message_id INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_text TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(event_id, notification_type, recipient_scope)
                );

                CREATE TABLE IF NOT EXISTS source_cursors (
                    source TEXT PRIMARY KEY,
                    listing_message_id INTEGER NOT NULL DEFAULT 0,
                    direct_message_id INTEGER NOT NULL DEFAULT 0,
                    last_verified_at TEXT,
                    last_progress_at TEXT,
                    stale_listing_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_stale_count INTEGER NOT NULL DEFAULT 0,
                    recovered_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                """
            )
            cursor_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(source_cursors)").fetchall()
            }
            if "consecutive_stale_count" not in cursor_columns:
                db.execute(
                    """
                    ALTER TABLE source_cursors
                    ADD COLUMN consecutive_stale_count INTEGER NOT NULL DEFAULT 0
                    """
                )
            db.execute(
                """
                INSERT INTO metadata(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _transition_id(
        event_id: str,
        stage: str,
        dedupe_key: str,
    ) -> str:
        raw = "\x1f".join((event_id, stage, dedupe_key))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _outbox_id(event_id: str, kind: str, scope: str) -> str:
        raw = "\x1f".join((event_id, kind, scope))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def resolve_event_id(self, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        with self._connect() as db:
            alias = db.execute(
                "SELECT event_id FROM event_aliases WHERE alias=?",
                (candidate,),
            ).fetchone()
            return str(alias["event_id"]) if alias else candidate

    def _append_transition(
        self,
        db: sqlite3.Connection,
        event_id: str,
        stage: str,
        occurred_at: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> bool:
        transition_id = self._transition_id(event_id, stage, dedupe_key)
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO event_transitions(
                transition_id, event_id, stage, occurred_at, dedupe_key, payload
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                event_id,
                stage,
                occurred_at,
                dedupe_key,
                _json(payload),
            ),
        )
        return cursor.rowcount > 0

    def _enqueue(
        self,
        db: sqlite3.Connection,
        event_id: str,
        kind: str,
        scope: str,
        payload: dict[str, Any],
        current: str,
        *,
        status: str = "pending",
    ) -> str:
        outbox_id = self._outbox_id(event_id, kind, scope)
        db.execute(
            """
            INSERT INTO outbox(
                outbox_id, event_id, kind, scope, payload, status,
                available_at, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, kind, scope) DO UPDATE SET
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (
                outbox_id,
                event_id,
                kind,
                scope,
                _json(payload),
                status,
                current,
                current,
                current,
            ),
        )
        return outbox_id

    def _enqueue_ledger_sync(
        self,
        db: sqlite3.Connection,
        event_id: str,
        current: str,
    ) -> None:
        outbox_id = self._outbox_id(
            event_id,
            "github_ledger_sync",
            "runtime-ledger",
        )
        db.execute(
            """
            INSERT INTO outbox(
                outbox_id, event_id, kind, scope, payload, status,
                available_at, created_at, updated_at
            ) VALUES(?, ?, 'github_ledger_sync', 'runtime-ledger', ?,
                     'pending', ?, ?, ?)
            ON CONFLICT(event_id, kind, scope) DO UPDATE SET
                status='pending',
                available_at=excluded.available_at,
                claim_token=NULL,
                claimed_at=NULL,
                claim_expires_at=NULL,
                completed_at=NULL,
                last_error=NULL,
                updated_at=excluded.updated_at
            """,
            (
                outbox_id,
                event_id,
                _json({"event_id": event_id}),
                current,
                current,
                current,
            ),
        )

    def _promote_provisional(
        self,
        db: sqlite3.Connection,
        provisional_id: str,
        event_id: str,
        generation_id: str,
        current: str,
    ) -> None:
        if not provisional_id or provisional_id == event_id:
            return
        provisional = db.execute(
            "SELECT * FROM events WHERE event_id=?",
            (provisional_id,),
        ).fetchone()
        if provisional is None:
            return
        existing = db.execute(
            "SELECT event_id FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing is None:
            db.execute(
                """
                UPDATE events
                SET event_id=?, generation_id=?, provisional=0, updated_at=?
                WHERE event_id=?
                """,
                (event_id, generation_id, current, provisional_id),
            )
            for row in db.execute(
                "SELECT outbox_id, kind, scope FROM outbox WHERE event_id=?",
                (event_id,),
            ).fetchall():
                db.execute(
                    "UPDATE outbox SET outbox_id=? WHERE outbox_id=?",
                    (
                        self._outbox_id(event_id, row["kind"], row["scope"]),
                        row["outbox_id"],
                    ),
                )
            for row in db.execute(
                """
                SELECT transition_id, stage, dedupe_key
                FROM event_transitions WHERE event_id=?
                """,
                (event_id,),
            ).fetchall():
                db.execute(
                    """
                    UPDATE event_transitions SET transition_id=?
                    WHERE transition_id=?
                    """,
                    (
                        self._transition_id(
                            event_id,
                            row["stage"],
                            row["dedupe_key"],
                        ),
                        row["transition_id"],
                    ),
                )
            for row in db.execute(
                "SELECT * FROM account_attempts WHERE event_id=?",
                (event_id,),
            ).fetchall():
                new_id = hashlib.sha256(
                    "\x1f".join(
                        (
                            event_id,
                            str(row["owner_id"]),
                            str(row["account_key"]),
                            str(row["recorded_at"]),
                            str(row["status"]),
                            str(row["finished_at"] or ""),
                        )
                    ).encode("utf-8")
                ).hexdigest()
                db.execute(
                    "UPDATE account_attempts SET attempt_id=? WHERE attempt_id=?",
                    (new_id, row["attempt_id"]),
                )
            for row in db.execute(
                """
                SELECT delivery_id, notification_type, recipient_scope
                FROM notification_deliveries WHERE event_id=?
                """,
                (event_id,),
            ).fetchall():
                new_id = hashlib.sha256(
                    "\x1f".join(
                        (
                            event_id,
                            str(row["notification_type"]),
                            str(row["recipient_scope"]),
                        )
                    ).encode("utf-8")
                ).hexdigest()
                db.execute(
                    """
                    UPDATE notification_deliveries SET delivery_id=?
                    WHERE delivery_id=?
                    """,
                    (new_id, row["delivery_id"]),
                )
        else:
            for row in db.execute(
                "SELECT * FROM event_transitions WHERE event_id=?",
                (provisional_id,),
            ).fetchall():
                transition_id = self._transition_id(
                    event_id,
                    row["stage"],
                    row["dedupe_key"],
                )
                db.execute(
                    """
                    INSERT OR IGNORE INTO event_transitions(
                        transition_id, event_id, stage, occurred_at,
                        dedupe_key, payload
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transition_id,
                        event_id,
                        row["stage"],
                        row["occurred_at"],
                        row["dedupe_key"],
                        row["payload"],
                    ),
                )
            for row in db.execute(
                "SELECT * FROM outbox WHERE event_id=?",
                (provisional_id,),
            ).fetchall():
                self._enqueue(
                    db,
                    event_id,
                    row["kind"],
                    row["scope"],
                    json.loads(str(row["payload"] or "{}")),
                    current,
                    status=row["status"],
                )
            for row in db.execute(
                "SELECT * FROM account_attempts WHERE event_id=?",
                (provisional_id,),
            ).fetchall():
                attempt_id = hashlib.sha256(
                    "\x1f".join(
                        (
                            event_id,
                            str(row["owner_id"]),
                            str(row["account_key"]),
                            str(row["recorded_at"]),
                            str(row["status"]),
                            str(row["finished_at"] or ""),
                        )
                    ).encode("utf-8")
                ).hexdigest()
                db.execute(
                    """
                    INSERT OR IGNORE INTO account_attempts(
                        attempt_id, event_id, owner_id, account_key, status,
                        confirmation, confidence, started_at, finished_at,
                        error_text, artifact_url, recorded_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        event_id,
                        row["owner_id"],
                        row["account_key"],
                        row["status"],
                        row["confirmation"],
                        row["confidence"],
                        row["started_at"],
                        row["finished_at"],
                        row["error_text"],
                        row["artifact_url"],
                        row["recorded_at"],
                    ),
                )
            for row in db.execute(
                "SELECT * FROM account_results WHERE event_id=?",
                (provisional_id,),
            ).fetchall():
                target = db.execute(
                    """
                    SELECT confidence, status FROM account_results
                    WHERE event_id=? AND owner_id=? AND account_key=?
                    """,
                    (event_id, row["owner_id"], row["account_key"]),
                ).fetchone()
                target_success = bool(
                    target
                    and str(target["status"]).casefold() in SUCCESS_STATUSES
                )
                source_success = str(row["status"]).casefold() in SUCCESS_STATUSES
                if (
                    target is None
                    or (source_success and not target_success)
                    or (
                        source_success == target_success
                        and int(row["confidence"]) >= int(target["confidence"])
                    )
                ):
                    db.execute(
                        """
                        INSERT INTO account_results(
                            event_id, owner_id, account_key, account_label,
                            status, confirmation, confidence, started_at,
                            finished_at, error_text, attempt_count,
                            artifact_url, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id, owner_id, account_key)
                        DO UPDATE SET
                            account_label=excluded.account_label,
                            status=excluded.status,
                            confirmation=excluded.confirmation,
                            confidence=excluded.confidence,
                            started_at=COALESCE(
                                account_results.started_at,
                                excluded.started_at
                            ),
                            finished_at=excluded.finished_at,
                            error_text=excluded.error_text,
                            attempt_count=MAX(
                                account_results.attempt_count,
                                excluded.attempt_count
                            ),
                            artifact_url=COALESCE(
                                excluded.artifact_url,
                                account_results.artifact_url
                            ),
                            updated_at=excluded.updated_at
                        """,
                        (
                            event_id,
                            row["owner_id"],
                            row["account_key"],
                            row["account_label"],
                            row["status"],
                            row["confirmation"],
                            row["confidence"],
                            row["started_at"],
                            row["finished_at"],
                            row["error_text"],
                            row["attempt_count"],
                            row["artifact_url"],
                            current,
                        ),
                    )
            for row in db.execute(
                "SELECT * FROM notification_deliveries WHERE event_id=?",
                (provisional_id,),
            ).fetchall():
                raw = "\x1f".join(
                    (
                        event_id,
                        str(row["notification_type"]),
                        str(row["recipient_scope"]),
                    )
                )
                delivery_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                db.execute(
                    """
                    INSERT INTO notification_deliveries(
                        delivery_id, event_id, notification_type,
                        recipient_scope, status, created_at, sent_at,
                        telegram_message_id, retry_count, error_text, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, notification_type, recipient_scope)
                    DO UPDATE SET
                        status=CASE
                            WHEN notification_deliveries.sent_at IS NOT NULL
                              OR notification_deliveries.telegram_message_id
                                 IS NOT NULL
                            THEN notification_deliveries.status
                            ELSE excluded.status END,
                        sent_at=COALESCE(
                            notification_deliveries.sent_at,
                            excluded.sent_at
                        ),
                        telegram_message_id=COALESCE(
                            notification_deliveries.telegram_message_id,
                            excluded.telegram_message_id
                        ),
                        retry_count=MAX(
                            notification_deliveries.retry_count,
                            excluded.retry_count
                        ),
                        updated_at=excluded.updated_at
                    """,
                    (
                        delivery_id,
                        event_id,
                        row["notification_type"],
                        row["recipient_scope"],
                        row["status"],
                        row["created_at"],
                        row["sent_at"],
                        row["telegram_message_id"],
                        row["retry_count"],
                        row["error_text"],
                        current,
                    ),
                )
            db.execute(
                "UPDATE event_aliases SET event_id=? WHERE event_id=?",
                (event_id, provisional_id),
            )
            db.execute(
                """
                DELETE FROM events WHERE event_id=?
                """,
                (provisional_id,),
            )
        db.execute(
            """
            INSERT INTO event_aliases(alias, event_id, migrated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                event_id=excluded.event_id,
                migrated_at=excluded.migrated_at
            """,
            (provisional_id, event_id, current),
        )

    def prepare_event(
        self,
        entry: dict[str, Any],
        *,
        detected_at: Any = None,
        enqueue_participation: bool = True,
        enqueue_notification: bool = True,
        discovery_reason: str = "monitor",
    ) -> str:
        wheel_key = str(
            entry.get("wheel_key") or entry.get("identifier") or ""
        ).strip().casefold()
        action_id = _safe_int(entry.get("action_id"))
        server_start_at = canonical_start_at(entry.get("server_start_at"))
        generation_id = canonical_generation_id(
            wheel_key,
            action_id,
            server_start_at,
        )
        canonical = canonical_event_id(wheel_key, action_id, server_start_at)
        provisional = provisional_event_id(
            wheel_key,
            entry.get("source"),
            entry.get("message_id"),
            entry.get("message_date"),
            action_id,
        )
        event_id = canonical or provisional
        if not event_id:
            raise ValueError("wheel_key is required for durable event identity")

        current = _iso()
        detected = canonical_start_at(detected_at) or current
        message_date = canonical_start_at(entry.get("message_date"))
        referral = bool(entry.get("referral_restricted"))
        verification = str(entry.get("verification_status") or "")
        status = str(entry.get("status") or "discovered")
        payload = {
            "event_id": event_id,
            "generation_id": generation_id,
            "wheel_key": wheel_key,
            "action_id": action_id,
            "server_start_at": server_start_at,
            "source": str(entry.get("source") or ""),
            "source_message_id": _safe_int(entry.get("message_id")),
            "source_message_date": message_date,
            "source_message_url": str(entry.get("message_url") or ""),
            "url": str(entry.get("url") or ""),
            "identifier": str(entry.get("identifier") or wheel_key),
            "message_text": str(entry.get("message_text") or "")[:4000],
            "deadline": canonical_start_at(entry.get("deadline")),
            "available_at": canonical_start_at(entry.get("available_at")),
            "expires_at": canonical_start_at(entry.get("expires_at")),
            "verification_status": verification,
            "status": status,
            "referral_restricted": referral,
            "discovery_reason": discovery_reason,
        }
        appended: list[dict[str, Any]] = []
        with self.transaction() as db:
            if canonical:
                provisional_candidates = {
                    provisional,
                    provisional_event_id(
                        wheel_key,
                        entry.get("source"),
                        entry.get("message_id"),
                        entry.get("message_date"),
                    ),
                }
                for candidate in provisional_candidates:
                    self._promote_provisional(
                        db,
                        candidate,
                        canonical,
                        generation_id,
                        current,
                    )
            existing = db.execute(
                "SELECT * FROM events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            discovered_at = (
                str(existing["discovered_at"])
                if existing is not None
                else detected
            )
            api_confirmed_at = (
                current
                if verification == "confirmed"
                else (
                    str(existing["api_confirmed_at"])
                    if existing is not None and existing["api_confirmed_at"]
                    else None
                )
            )
            db.execute(
                """
                INSERT INTO events(
                    event_id, generation_id, wheel_key, action_id,
                    server_start_at, source, source_message_id,
                    source_message_date, source_message_url, status,
                    referral_restricted, provisional, discovered_at,
                    api_confirmed_at, persisted_at, dispatch_queued_at,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    generation_id=CASE
                        WHEN excluded.generation_id <> '' THEN excluded.generation_id
                        ELSE events.generation_id END,
                    action_id=COALESCE(excluded.action_id, events.action_id),
                    server_start_at=COALESCE(
                        NULLIF(excluded.server_start_at, ''),
                        events.server_start_at
                    ),
                    source=COALESCE(NULLIF(excluded.source, ''), events.source),
                    source_message_id=COALESCE(
                        excluded.source_message_id,
                        events.source_message_id
                    ),
                    source_message_date=COALESCE(
                        NULLIF(excluded.source_message_date, ''),
                        events.source_message_date
                    ),
                    source_message_url=COALESCE(
                        NULLIF(excluded.source_message_url, ''),
                        events.source_message_url
                    ),
                    status=excluded.status,
                    referral_restricted=MAX(
                        events.referral_restricted,
                        excluded.referral_restricted
                    ),
                    provisional=MIN(events.provisional, excluded.provisional),
                    api_confirmed_at=COALESCE(
                        events.api_confirmed_at,
                        excluded.api_confirmed_at
                    ),
                    dispatch_queued_at=COALESCE(
                        events.dispatch_queued_at,
                        excluded.dispatch_queued_at
                    ),
                    updated_at=excluded.updated_at
                """,
                (
                    event_id,
                    generation_id,
                    wheel_key,
                    action_id,
                    server_start_at or None,
                    payload["source"],
                    payload["source_message_id"],
                    message_date or None,
                    payload["source_message_url"],
                    status,
                    int(referral),
                    int(not bool(canonical)),
                    discovered_at,
                    api_confirmed_at,
                    current,
                    current if enqueue_participation else None,
                    current,
                    current,
                ),
            )
            for alias in legacy_event_aliases(entry, wheel_key=wheel_key):
                if alias == event_id:
                    continue
                db.execute(
                    """
                    INSERT INTO event_aliases(alias, event_id, migrated_at)
                    VALUES(?, ?, ?)
                    ON CONFLICT(alias) DO UPDATE SET
                        event_id=excluded.event_id,
                        migrated_at=excluded.migrated_at
                    """,
                    (alias, event_id, current),
                )
            stages = [
                ("source_discovered", detected, discovery_reason),
                ("persisted", current, "durable-event"),
            ]
            if enqueue_participation:
                stages.append(("dispatch_queued", current, "auto-participation"))
            for stage, occurred, dedupe in stages:
                if self._append_transition(
                    db,
                    event_id,
                    stage,
                    occurred,
                    payload,
                    dedupe_key=dedupe,
                ):
                    appended.append(
                        {
                            "event_id": event_id,
                            "stage": stage,
                            "occurred_at": occurred,
                            "payload": payload,
                        }
                    )
            if api_confirmed_at and self._append_transition(
                db,
                event_id,
                "api_confirmed",
                api_confirmed_at,
                payload,
                dedupe_key=f"{action_id}:{server_start_at}",
            ):
                appended.append(
                    {
                        "event_id": event_id,
                        "stage": "api_confirmed",
                        "occurred_at": api_confirmed_at,
                        "payload": payload,
                    }
                )
            if enqueue_participation:
                self._enqueue(
                    db,
                    event_id,
                    "auto_participation",
                    "all_enabled_accounts",
                    payload,
                    current,
                )
            self._enqueue(
                db,
                event_id,
                "new_wheel_notification",
                "configured_recipients",
                payload,
                current,
                status=(
                    "suppressed_referral"
                    if referral or not enqueue_notification
                    else "pending"
                ),
            )
            self._enqueue_ledger_sync(db, event_id, current)
        for row in appended:
            self._append_jsonl(self.audit_path, row)
        return event_id

    def record_transition(
        self,
        event_id: str,
        stage: str,
        *,
        occurred_at: Any = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str = "default",
    ) -> bool:
        resolved = self.resolve_event_id(event_id)
        timestamp = canonical_start_at(occurred_at) or _iso()
        data = dict(payload or {})
        with self.transaction() as db:
            inserted = self._append_transition(
                db,
                resolved,
                stage,
                timestamp,
                data,
                dedupe_key=dedupe_key,
            )
            column = {
                "dispatch_queued": "dispatch_queued_at",
                "workflow_started": "workflow_started_at",
                "browser_started": "browser_started_at",
                "notification_sent": "notification_sent_at",
                "wheel_closed": "closed_at",
                "final_sent": "final_sent_at",
            }.get(stage)
            if column:
                db.execute(
                    f"""
                    UPDATE events SET {column}=COALESCE({column}, ?), updated_at=?
                    WHERE event_id=?
                    """,
                    (timestamp, timestamp, resolved),
                )
            self._enqueue_ledger_sync(db, resolved, _iso())
        if inserted:
            self._append_jsonl(
                self.audit_path,
                {
                    "event_id": resolved,
                    "stage": stage,
                    "occurred_at": timestamp,
                    "payload": data,
                },
            )
        return inserted

    def record_account_result(
        self,
        event_id: str,
        *,
        owner_id: str,
        account_key: str,
        account_label: str,
        status: str,
        confirmation: str = "",
        started_at: Any = None,
        finished_at: Any = None,
        error_text: str = "",
        attempt_count: int = 1,
        artifact_url: str = "",
    ) -> bool:
        resolved = self.resolve_event_id(event_id)
        if not owner_id or not account_key or not account_label:
            raise ValueError("owner_id, account_key and account_label are required")
        current = _iso()
        started = canonical_start_at(started_at)
        finished = canonical_start_at(finished_at) or current
        confidence = status_confidence(status, confirmation)
        attempt_id = hashlib.sha256(
            "\x1f".join(
                (
                    resolved,
                    owner_id,
                    account_key,
                    str(attempt_count),
                    status,
                    finished,
                )
            ).encode("utf-8")
        ).hexdigest()
        accepted = False
        with self.transaction() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO account_attempts(
                    attempt_id, event_id, owner_id, account_key, status,
                    confirmation, confidence, started_at, finished_at,
                    error_text, artifact_url, recorded_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    resolved,
                    owner_id,
                    account_key,
                    status,
                    confirmation,
                    confidence,
                    started or None,
                    finished,
                    error_text[:1000],
                    artifact_url[:1000],
                    current,
                ),
            )
            previous = db.execute(
                """
                SELECT confidence, status FROM account_results
                WHERE event_id=? AND owner_id=? AND account_key=?
                """,
                (resolved, owner_id, account_key),
            ).fetchone()
            previous_confidence = int(previous["confidence"]) if previous else -1
            previous_success = (
                str(previous["status"]).casefold() in SUCCESS_STATUSES
                if previous
                else False
            )
            incoming_success = str(status).casefold() in SUCCESS_STATUSES
            accepted = (
                previous is None
                or (incoming_success and not previous_success)
                or (
                    previous_success == incoming_success
                    and confidence > previous_confidence
                )
                or (
                    previous_success == incoming_success
                    and confidence == previous_confidence
                )
            )
            if accepted:
                db.execute(
                    """
                    INSERT INTO account_results(
                        event_id, owner_id, account_key, account_label, status,
                        confirmation, confidence, started_at, finished_at,
                        error_text, attempt_count, artifact_url, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, owner_id, account_key) DO UPDATE SET
                        account_label=excluded.account_label,
                        status=excluded.status,
                        confirmation=excluded.confirmation,
                        confidence=excluded.confidence,
                        started_at=COALESCE(
                            account_results.started_at,
                            excluded.started_at
                        ),
                        finished_at=excluded.finished_at,
                        error_text=excluded.error_text,
                        attempt_count=MAX(
                            account_results.attempt_count,
                            excluded.attempt_count
                        ),
                        artifact_url=COALESCE(
                            NULLIF(excluded.artifact_url, ''),
                            account_results.artifact_url
                        ),
                        updated_at=excluded.updated_at
                    """,
                    (
                        resolved,
                        owner_id,
                        account_key,
                        account_label,
                        status,
                        confirmation,
                        confidence,
                        started or None,
                        finished,
                        error_text[:1000],
                        max(1, int(attempt_count)),
                        artifact_url[:1000],
                        current,
                    ),
                )
                self._append_transition(
                    db,
                    resolved,
                    "account_result",
                    finished,
                    {
                        "owner_id": owner_id,
                        "account_key": account_key,
                        "account_label": account_label,
                        "status": status,
                        "confirmation": confirmation,
                        "attempt_count": max(1, int(attempt_count)),
                        "artifact_url": artifact_url[:1000],
                    },
                    dedupe_key=f"{owner_id}:{account_key}:{attempt_id}",
                )
            self._enqueue_ledger_sync(db, resolved, current)
        return accepted

    def record_notification(
        self,
        event_id: str | None,
        *,
        notification_type: str,
        recipient_scope: str,
        status: str,
        telegram_message_id: Any = None,
        retry_count: int = 0,
        error_text: str = "",
        created_at: Any = None,
        sent_at: Any = None,
    ) -> str:
        resolved = self.resolve_event_id(event_id or "") if event_id else ""
        current = _iso()
        created = canonical_start_at(created_at) or current
        sent = canonical_start_at(sent_at)
        message_id = _safe_int(telegram_message_id)
        raw = "\x1f".join((resolved, notification_type, recipient_scope))
        delivery_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO notification_deliveries(
                    delivery_id, event_id, notification_type, recipient_scope,
                    status, created_at, sent_at, telegram_message_id,
                    retry_count, error_text, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, notification_type, recipient_scope)
                DO UPDATE SET
                    status=CASE
                        WHEN notification_deliveries.sent_at IS NOT NULL
                          OR notification_deliveries.telegram_message_id IS NOT NULL
                        THEN notification_deliveries.status
                        ELSE excluded.status
                    END,
                    sent_at=COALESCE(
                        notification_deliveries.sent_at,
                        excluded.sent_at
                    ),
                    telegram_message_id=COALESCE(
                        notification_deliveries.telegram_message_id,
                        excluded.telegram_message_id
                    ),
                    retry_count=MAX(
                        notification_deliveries.retry_count,
                        excluded.retry_count
                    ),
                    error_text=excluded.error_text,
                    updated_at=excluded.updated_at
                """,
                (
                    delivery_id,
                    resolved or None,
                    notification_type,
                    recipient_scope,
                    status,
                    created,
                    sent or None,
                    message_id,
                    max(0, int(retry_count)),
                    error_text[:1000],
                    current,
                ),
            )
            if resolved and sent:
                self._append_transition(
                    db,
                    resolved,
                    "notification_sent",
                    sent,
                    {
                        "notification_type": notification_type,
                        "recipient_scope": recipient_scope,
                        "telegram_message_id": message_id,
                        "retry_count": max(0, int(retry_count)),
                        "status": status,
                    },
                    dedupe_key=f"{notification_type}:{recipient_scope}",
                )
                db.execute(
                    """
                    UPDATE events
                    SET notification_sent_at=COALESCE(notification_sent_at, ?),
                        updated_at=?
                    WHERE event_id=?
                    """,
                    (sent, current, resolved),
                )
            if resolved:
                self._enqueue_ledger_sync(db, resolved, current)
        self._append_jsonl(
            self.notification_audit_path,
            {
                "delivery_id": delivery_id,
                "event_id": resolved or None,
                "notification_type": notification_type,
                "recipient_scope": recipient_scope,
                "created_at": created,
                "sent_at": sent or None,
                "telegram_message_id": message_id,
                "retry_count": max(0, int(retry_count)),
                "status": status,
                "error_text": error_text[:1000],
            },
        )
        return delivery_id

    def mark_event_outbox(
        self,
        event_id: str,
        kind: str,
        *,
        status: str,
        error_text: str = "",
    ) -> bool:
        resolved = self.resolve_event_id(event_id)
        current = _iso()
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE outbox
                SET status=?, last_error=?, claim_token=NULL, claimed_at=NULL,
                    claim_expires_at=NULL,
                    completed_at=CASE
                        WHEN ? IN ('completed', 'suppressed') THEN ?
                        ELSE completed_at
                    END,
                    updated_at=?
                WHERE event_id=? AND kind=?
                """,
                (
                    status,
                    error_text[:1000],
                    status,
                    current,
                    current,
                    resolved,
                    kind,
                ),
            )
        return cursor.rowcount > 0

    def claim_outbox(
        self,
        kinds: set[str] | None = None,
        *,
        event_ids: set[str] | None = None,
        limit: int = 20,
        lease_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        current = _now()
        now_text = _iso(current)
        expires = _iso(current + timedelta(seconds=max(30, lease_seconds)))
        claim_token = uuid.uuid4().hex
        with self.transaction() as db:
            params: list[Any] = [now_text, now_text]
            where = """
                status IN ('pending', 'retry')
                AND available_at <= ?
                AND (claim_expires_at IS NULL OR claim_expires_at <= ?)
            """
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                where += f" AND kind IN ({placeholders})"
                params.extend(sorted(kinds))
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                where += f" AND event_id IN ({placeholders})"
                params.extend(sorted(event_ids))
            params.append(max(1, int(limit)))
            rows = db.execute(
                f"""
                SELECT * FROM outbox
                WHERE {where}
                ORDER BY created_at, outbox_id
                LIMIT ?
                """,
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                db.execute(
                    """
                    UPDATE outbox SET
                        status='claimed',
                        claimed_at=?,
                        claim_token=?,
                        claim_expires_at=?,
                        attempts=attempts+1,
                        updated_at=?
                    WHERE outbox_id=?
                    """,
                    (now_text, claim_token, expires, now_text, row["outbox_id"]),
                )
                item = dict(row)
                item["payload"] = json.loads(str(item.get("payload") or "{}"))
                item["claim_token"] = claim_token
                item["claim_expires_at"] = expires
                item["attempts"] = int(item.get("attempts", 0)) + 1
                result.append(item)
            return result

    def complete_outbox(self, outbox_id: str, claim_token: str) -> bool:
        current = _iso()
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE outbox SET
                    status='completed',
                    completed_at=?,
                    claim_token=NULL,
                    claim_expires_at=NULL,
                    last_error=NULL,
                    updated_at=?
                WHERE outbox_id=? AND claim_token=?
                """,
                (current, current, outbox_id, claim_token),
            )
            return cursor.rowcount > 0

    def fail_outbox(
        self,
        outbox_id: str,
        claim_token: str,
        error_text: str,
        *,
        retry_after_seconds: int = 30,
    ) -> bool:
        current = _now()
        available = _iso(current + timedelta(seconds=max(1, retry_after_seconds)))
        now_text = _iso(current)
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE outbox SET
                    status='retry',
                    available_at=?,
                    claim_token=NULL,
                    claim_expires_at=NULL,
                    last_error=?,
                    updated_at=?
                WHERE outbox_id=? AND claim_token=?
                """,
                (
                    available,
                    error_text[:1000],
                    now_text,
                    outbox_id,
                    claim_token,
                ),
            )
            return cursor.rowcount > 0

    def update_source_cursor(
        self,
        source: str,
        *,
        listing_message_id: int,
        direct_message_id: int,
        recovered_count: int = 0,
        stale_listing: bool = False,
    ) -> None:
        normalized = str(source or "").strip().lstrip("@").casefold()
        if not normalized:
            return
        current = _iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO source_cursors(
                    source, listing_message_id, direct_message_id,
                    last_verified_at, last_progress_at, stale_listing_count,
                    consecutive_stale_count, recovered_count, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    listing_message_id=MAX(
                        source_cursors.listing_message_id,
                        excluded.listing_message_id
                    ),
                    direct_message_id=MAX(
                        source_cursors.direct_message_id,
                        excluded.direct_message_id
                    ),
                    last_verified_at=excluded.last_verified_at,
                    last_progress_at=CASE
                        WHEN excluded.direct_message_id >
                             source_cursors.direct_message_id
                        THEN excluded.last_progress_at
                        ELSE source_cursors.last_progress_at END,
                    stale_listing_count=source_cursors.stale_listing_count +
                        excluded.stale_listing_count,
                    consecutive_stale_count=CASE
                        WHEN excluded.consecutive_stale_count > 0
                        THEN source_cursors.consecutive_stale_count + 1
                        ELSE 0 END,
                    recovered_count=source_cursors.recovered_count +
                        excluded.recovered_count,
                    updated_at=excluded.updated_at
                """,
                (
                    normalized,
                    max(0, int(listing_message_id)),
                    max(0, int(direct_message_id)),
                    current,
                    current,
                    int(stale_listing),
                    int(stale_listing),
                    max(0, int(recovered_count)),
                    current,
                ),
            )

    def import_legacy_state(self, state: dict[str, Any]) -> dict[str, int]:
        imported = 0
        aliases = 0
        results_imported = 0
        active = state.get("active_wheels")
        active = active if isinstance(active, dict) else {}
        active_by_action: dict[tuple[str, int], dict[str, Any]] = {}
        for raw_key, raw in active.items():
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            wheel = str(
                item.get("wheel_key") or item.get("identifier") or raw_key
            ).strip().casefold()
            item["wheel_key"] = wheel
            item.setdefault("identifier", wheel)
            action = _safe_int(item.get("action_id"))
            if wheel and action:
                active_by_action[(wheel, action)] = item

        candidates: list[dict[str, Any]] = []
        observations = state.get("wheel_generation_observations")
        if isinstance(observations, dict):
            for raw in observations.values():
                if not isinstance(raw, dict):
                    continue
                entry = dict(raw)
                wheel = str(
                    entry.get("wheel_key") or entry.get("identifier") or ""
                ).strip().casefold()
                action = _safe_int(entry.get("action_id"))
                current_active = active_by_action.get((wheel, action or 0))
                if (
                    current_active
                    and not canonical_start_at(entry.get("server_start_at"))
                ):
                    first_seen = entry.get("first_seen_at")
                    statuses = entry.get("statuses")
                    entry.update(current_active)
                    entry["first_seen_at"] = first_seen
                    entry["statuses"] = statuses
                candidates.append(entry)
        observed_ids = {
            event_id_from_entry(item)
            for item in candidates
            if event_id_from_entry(item)
        }
        for entry in active_by_action.values():
            if event_id_from_entry(entry) not in observed_ids:
                candidates.append(entry)

        for entry in candidates:
            entry.setdefault("status", "observed")
            entry.setdefault("identifier", entry.get("wheel_key"))
            before = str(entry.get("generation_id") or "")
            try:
                event_id = self.prepare_event(
                    entry,
                    detected_at=entry.get("first_seen_at"),
                    enqueue_participation=False,
                    enqueue_notification=False,
                    discovery_reason="legacy_observation_import",
                )
            except ValueError:
                continue
            imported += 1
            if before and event_id != f"evt:{before}":
                aliases += 1
            notified_at = entry.get("first_notified_at")
            if notified_at:
                self.record_notification(
                    event_id,
                    notification_type="new_wheel",
                    recipient_scope="legacy_configured_recipients",
                    status="legacy_sent_unverified",
                    sent_at=notified_at,
                )
        legacy_results = state.get("auto_participation_events")
        if isinstance(legacy_results, dict):
            for raw_token, raw in legacy_results.items():
                if not isinstance(raw, dict):
                    continue
                base_token = str(raw_token).split("#account:", 1)[0]
                explicit = str(raw.get("event_token") or "").split(
                    "#account:",
                    1,
                )[0]
                resolved = self.resolve_event_id(explicit or base_token)
                if not resolved.startswith(("evt:", "pending:")):
                    continue
                account_key = str(raw.get("account_key") or "").strip()
                if not account_key and "#account:" in str(raw_token):
                    account_key = str(raw_token).split("#account:", 1)[1].strip()
                account_key = account_key or "legacy_primary"
                owner_id = str(
                    raw.get("account_owner")
                    or raw.get("owner_id")
                    or "legacy_unknown_owner"
                ).strip()
                label = str(
                    raw.get("account_label")
                    or raw.get("display_name")
                    or account_key
                ).strip()
                status = str(raw.get("status") or "unknown").strip()
                try:
                    attempt_count = int(raw.get("attempt_count", 1) or 1)
                except (TypeError, ValueError):
                    attempt_count = 1
                try:
                    self.record_account_result(
                        resolved,
                        owner_id=owner_id,
                        account_key=account_key,
                        account_label=label,
                        status=status,
                        confirmation=str(
                            raw.get("confirmation")
                            or raw.get("confirmation_method")
                            or "legacy_state_import"
                        ),
                        started_at=raw.get("started_at") or raw.get("attempted_at"),
                        finished_at=(
                            raw.get("finished_at")
                            or raw.get("attempted_at")
                            or raw.get("recorded_at")
                        ),
                        error_text=str(
                            raw.get("error_text")
                            or raw.get("detail")
                            or ""
                        ),
                        attempt_count=max(1, attempt_count),
                        artifact_url=str(raw.get("artifact_url") or ""),
                    )
                except (KeyError, ValueError):
                    continue
                results_imported += 1
        return {
            "events_imported": imported,
            "legacy_aliases": aliases,
            "account_results_imported": results_imported,
        }

    def day_report(self, day: str) -> list[dict[str, Any]]:
        prefix = str(day).strip()[:10]
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM events
                WHERE substr(COALESCE(server_start_at, discovered_at), 1, 10)=?
                ORDER BY COALESCE(server_start_at, discovered_at), event_id
                """,
                (prefix,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                event = dict(row)
                event["account_results"] = [
                    dict(item)
                    for item in db.execute(
                        """
                        SELECT * FROM account_results
                        WHERE event_id=?
                        ORDER BY owner_id, account_key
                        """,
                        (event["event_id"],),
                    ).fetchall()
                ]
                event["notifications"] = [
                    dict(item)
                    for item in db.execute(
                        """
                        SELECT * FROM notification_deliveries
                        WHERE event_id=?
                        ORDER BY created_at, delivery_id
                        """,
                        (event["event_id"],),
                    ).fetchall()
                ]
                result.append(event)
            return result

    def event_snapshot(self, event_id: str) -> dict[str, Any]:
        resolved = self.resolve_event_id(event_id)
        with self._connect() as db:
            event = db.execute(
                "SELECT * FROM events WHERE event_id=?",
                (resolved,),
            ).fetchone()
            if event is None:
                raise KeyError(resolved)
            result = dict(event)
            result["aliases"] = [
                str(row["alias"])
                for row in db.execute(
                    "SELECT alias FROM event_aliases WHERE event_id=? ORDER BY alias",
                    (resolved,),
                ).fetchall()
            ]
            result["transitions"] = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT transition_id, stage, occurred_at, dedupe_key, payload
                    FROM event_transitions
                    WHERE event_id=?
                    ORDER BY sequence
                    """,
                    (resolved,),
                ).fetchall()
            ]
            for row in result["transitions"]:
                row["payload"] = json.loads(str(row.get("payload") or "{}"))
            result["account_results"] = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT * FROM account_results
                    WHERE event_id=? ORDER BY owner_id, account_key
                    """,
                    (resolved,),
                ).fetchall()
            ]
            result["account_attempts"] = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT * FROM account_attempts
                    WHERE event_id=? ORDER BY recorded_at, attempt_id
                    """,
                    (resolved,),
                ).fetchall()
            ]
            result["notifications"] = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT * FROM notification_deliveries
                    WHERE event_id=? ORDER BY created_at, delivery_id
                    """,
                    (resolved,),
                ).fetchall()
            ]
            result["ledger_schema_version"] = SCHEMA_VERSION
            return result

    def health(self) -> dict[str, Any]:
        with self._connect() as db:
            event_count = int(db.execute("SELECT count(*) FROM events").fetchone()[0])
            pending = int(
                db.execute(
                    """
                    SELECT count(*) FROM outbox
                    WHERE status IN ('pending', 'retry', 'claimed')
                    """
                ).fetchone()[0]
            )
            oldest = db.execute(
                """
                SELECT min(created_at) FROM outbox
                WHERE status IN ('pending', 'retry', 'claimed')
                """
            ).fetchone()[0]
            pending_by_kind = {
                str(row["kind"]): int(row["count"])
                for row in db.execute(
                    """
                    SELECT kind, count(*) AS count FROM outbox
                    WHERE status IN ('pending', 'retry', 'claimed')
                    GROUP BY kind
                    """
                ).fetchall()
            }
            account_results = int(
                db.execute("SELECT count(*) FROM account_results").fetchone()[0]
            )
            notifications_sent = int(
                db.execute(
                    """
                    SELECT count(*) FROM notification_deliveries
                    WHERE sent_at IS NOT NULL OR telegram_message_id IS NOT NULL
                    """
                ).fetchone()[0]
            )
            latest_cursor = db.execute(
                "SELECT max(last_progress_at) FROM source_cursors"
            ).fetchone()[0]
            stale_sources = int(
                db.execute(
                    """
                    SELECT count(*) FROM source_cursors
                    WHERE consecutive_stale_count > 0
                    """
                ).fetchone()[0]
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "events": event_count,
                "pending_outbox": pending,
                "oldest_pending_at": oldest,
                "database_path": str(self.path),
                "pending_by_kind": pending_by_kind,
                "account_results": account_results,
                "notifications_sent": notifications_sent,
                "latest_source_progress_at": latest_cursor,
                "stale_sources": stale_sources,
                "process_health": "ok",
                "source_freshness": "degraded" if stale_sources else "ok",
                "discovery_health": "ok" if event_count else "unknown",
                "dispatch_health": (
                    "backlogged"
                    if pending_by_kind.get("auto_participation", 0)
                    else "ok"
                ),
                "browser_health": "ok" if account_results else "unknown",
                "notification_health": (
                    "ok" if notifications_sent else "unknown"
                ),
                "reconciliation_health": "enabled",
            }

    @staticmethod
    def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _json(value) + "\n"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
