from __future__ import annotations

import hashlib
import json
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .models import Delivery, DeliveryStatus, OutboxStatus, RelayEvent


def utcnow() -> datetime:
    return datetime.now(UTC)


FINAL_COMMENT_OUTCOMES = {
    "baseline",
    "before_start_boundary",
    "no_event",
    "processed",
    "invalid_block",
    "invalid_event",
    "untrusted_actor",
    "edited_after_processing",
}


class RelayDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS comments(
                    repository TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    comment_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    PRIMARY KEY(repository, pr_number, comment_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                    event_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_comment_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    target_role TEXT NOT NULL,
                    body TEXT NOT NULL,
                    delivery_token TEXT NOT NULL DEFAULT '',
                    decision_consumed_at TEXT,
                    required_apps_json TEXT NOT NULL,
                    strict_apps INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    lease_expires_at TEXT,
                    leased_by TEXT,
                    last_code TEXT,
                    last_detail TEXT,
                    observed_url TEXT,
                    observed_message_marker TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(event_id, target_role),
                    FOREIGN KEY(event_id) REFERENCES events(event_id)
                );
                CREATE TABLE IF NOT EXISTS decision_outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_key TEXT NOT NULL UNIQUE,
                    delivery_id INTEGER,
                    task_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    conversation_key TEXT,
                    assistant_message_id TEXT,
                    assistant_message_hash TEXT,
                    decision TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_marker TEXT NOT NULL UNIQUE,
                    event_payload_json TEXT NOT NULL,
                    comment_body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    github_comment_id INTEGER,
                    github_comment_url TEXT,
                    last_error_code TEXT,
                    last_error_detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(delivery_id) REFERENCES deliveries(id)
                );
                CREATE TABLE IF NOT EXISTS task_state(
                    task_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    last_event_id TEXT NOT NULL,
                    worker_role TEXT,
                    pr_number INTEGER,
                    sha TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS heartbeats(
                    installation_id TEXT PRIMARY KEY,
                    extension_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS role_endpoints(
                    installation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    url TEXT NOT NULL,
                    conversation_key TEXT,
                    tab_id INTEGER,
                    active INTEGER NOT NULL DEFAULT 0,
                    composer_ready INTEGER NOT NULL DEFAULT 0,
                    login_required INTEGER NOT NULL DEFAULT 0,
                    busy INTEGER NOT NULL DEFAULT 0,
                    extension_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY(installation_id, role)
                );
                CREATE TABLE IF NOT EXISTS alerts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    task_id TEXT,
                    pr_number INTEGER,
                    detail TEXT NOT NULL,
                    github_comment_url TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_deliveries_ready ON deliveries(status, available_at, id);
                CREATE INDEX IF NOT EXISTS idx_outbox_ready ON decision_outbox(status, available_at, id);
                CREATE INDEX IF NOT EXISTS idx_role_endpoints_role ON role_endpoints(role, last_seen, active);
                CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(resolved_at, code, task_id, pr_number);
                """
            )
            comments = self._columns(conn, "comments")
            if "retry_count" not in comments:
                conn.execute("ALTER TABLE comments ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
            if "last_error" not in comments:
                conn.execute("ALTER TABLE comments ADD COLUMN last_error TEXT")
            deliveries = self._columns(conn, "deliveries")
            for column in ["observed_url", "observed_message_marker", "decision_consumed_at"]:
                if column not in deliveries:
                    conn.execute(f"ALTER TABLE deliveries ADD COLUMN {column} TEXT")
            if "delivery_token" not in deliveries:
                conn.execute("ALTER TABLE deliveries ADD COLUMN delivery_token TEXT NOT NULL DEFAULT ''")
            rows = conn.execute("SELECT id FROM deliveries WHERE delivery_token='' OR delivery_token IS NULL").fetchall()
            for row in rows:
                conn.execute("UPDATE deliveries SET delivery_token=? WHERE id=?", (secrets.token_urlsafe(24), row[0]))
            # v1 classified task-file 404s as permanent protocol errors. In v2 these
            # are infrastructure/configuration failures and the original comment must
            # be replayable after task_ref or credentials are repaired.
            conn.execute(
                """UPDATE comments SET outcome='retryable_error',
                last_error=COALESCE(last_error,'migrated from v1 task-spec 404')
                WHERE outcome='invalid_event' AND EXISTS (
                  SELECT 1 FROM alerts a
                  WHERE a.pr_number=comments.pr_number
                    AND a.code='PROTOCOL_OR_STATE_INVALID'
                    AND a.detail LIKE '%' || comments.comment_id || '%'
                    AND a.detail LIKE '%.sat2/tasks/%'
                    AND a.detail LIKE '%404%'
                )"""
            )
            # A missing role binding is a local routing outage, not a permanent
            # scientific/control failure. Requeue v2.0 deliveries that were
            # terminally failed before role-aware leasing was introduced.
            now = utcnow().isoformat()
            conn.execute(
                """UPDATE deliveries SET status=?,attempt_count=0,available_at=?,lease_expires_at=NULL,leased_by=NULL,
                last_detail=COALESCE(last_detail,'requeued by role-routing migration'),updated_at=?
                WHERE status=? AND last_code IN ('ROLE_NOT_BOUND','ROLE_ENDPOINT_UNAVAILABLE')""",
                (DeliveryStatus.RETRY.value, now, now, DeliveryStatus.FAILED.value),
            )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version','4') ON CONFLICT(key) DO UPDATE SET value='4'"
            )

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def all_meta(self) -> dict[str, str]:
        with self.connect() as conn:
            return {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT * FROM meta ORDER BY key")}

    def comment_status(self, repository: str, pr_number: int, comment_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM comments WHERE repository=? AND pr_number=? AND comment_id=?",
                (repository, pr_number, comment_id),
            ).fetchone()

    def comment_is_final(self, row: sqlite3.Row | None, updated_at: str, body_hash: str) -> bool:
        if not row:
            return False
        return row["updated_at"] == updated_at and row["body_hash"] == body_hash and row["outcome"] in FINAL_COMMENT_OUTCOMES

    def record_comment(
        self,
        repository: str,
        pr_number: int,
        comment: dict[str, Any],
        outcome: str,
        error: str | None = None,
    ) -> None:
        body = str(comment.get("body") or "")
        retry_increment = 1 if outcome == "retryable_error" else 0
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO comments(repository,pr_number,comment_id,updated_at,body_hash,actor,processed_at,outcome,retry_count,last_error)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(repository,pr_number,comment_id) DO UPDATE SET
                  updated_at=excluded.updated_at, body_hash=excluded.body_hash, actor=excluded.actor,
                  processed_at=excluded.processed_at, outcome=excluded.outcome,
                  retry_count=comments.retry_count + ?, last_error=excluded.last_error""",
                (
                    repository,
                    pr_number,
                    int(comment["id"]),
                    str(comment.get("updated_at") or comment.get("created_at")),
                    hashlib.sha256(body.encode()).hexdigest(),
                    str((comment.get("user") or {}).get("login") or "unknown"),
                    utcnow().isoformat(),
                    outcome,
                    retry_increment,
                    error,
                    retry_increment,
                ),
            )

    def mark_comment_for_replay(self, repository: str, pr_number: int, comment_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE comments SET outcome='retryable_error',last_error='manual replay requested',processed_at=? WHERE repository=? AND pr_number=? AND comment_id=?",
                (utcnow().isoformat(), repository, pr_number, comment_id),
            )
            return cur.rowcount == 1

    def accept_event(
        self,
        event: RelayEvent,
        payload_json: str,
        *,
        new_state: str,
        worker_role: str | None,
        state_sha: str | None,
        target_role: str | None,
        body: str | None,
        delivery_token: str | None,
        required_apps: list[str] | None,
        strict_apps: bool,
        awaiting_approval: bool,
    ) -> tuple[bool, int | None]:
        now = utcnow().isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.repository,
                        event.pr_number,
                        event.task_id,
                        event.event_type.value,
                        payload_json,
                        int(event.source_comment_id or 0),
                        now,
                    ),
                )
                if cur.rowcount == 0:
                    conn.execute("COMMIT")
                    return False, None
                conn.execute(
                    """INSERT INTO task_state(task_id,state,last_event_id,worker_role,pr_number,sha,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,
                    last_event_id=excluded.last_event_id,worker_role=COALESCE(excluded.worker_role,task_state.worker_role),
                    pr_number=excluded.pr_number,sha=COALESCE(excluded.sha,task_state.sha),updated_at=excluded.updated_at""",
                    (event.task_id, new_state, event.event_id, worker_role, event.pr_number, state_sha, now),
                )
                delivery_id = None
                if target_role and body is not None:
                    status = DeliveryStatus.AWAITING_APPROVAL.value if awaiting_approval else DeliveryStatus.PENDING.value
                    dcur = conn.execute(
                        """INSERT INTO deliveries(event_id,target_role,body,delivery_token,required_apps_json,strict_apps,status,
                        attempt_count,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event.event_id,
                            target_role,
                            body,
                            delivery_token or secrets.token_urlsafe(24),
                            json.dumps(required_apps or []),
                            int(strict_apps),
                            status,
                            0,
                            now,
                            now,
                            now,
                        ),
                    )
                    delivery_id = int(dcur.lastrowid)
                conn.execute("COMMIT")
                return True, delivery_id
            except Exception:
                conn.execute("ROLLBACK")
                raise

    # Compatibility wrappers used by tests and migration tools.
    def insert_event(self, event: RelayEvent, payload_json: str) -> bool:
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.repository,
                        event.pr_number,
                        event.task_id,
                        event.event_type.value,
                        payload_json,
                        int(event.source_comment_id or 0),
                        utcnow().isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def enqueue_delivery(self, event_id: str, target_role: str, body: str, required_apps: list[str], strict_apps: bool, awaiting_approval: bool, delivery_token: str | None = None) -> int | None:
        now = utcnow().isoformat()
        status = DeliveryStatus.AWAITING_APPROVAL.value if awaiting_approval else DeliveryStatus.PENDING.value
        try:
            with self.connect() as conn:
                cursor = conn.execute(
                    """INSERT INTO deliveries(event_id,target_role,body,delivery_token,required_apps_json,strict_apps,status,
                    attempt_count,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, target_role, body, delivery_token or secrets.token_urlsafe(24), json.dumps(required_apps), int(strict_apps), status, 0, now, now, now),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def approve_delivery(self, delivery_id: int) -> bool:
        now = utcnow().isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE deliveries SET status=?,available_at=?,updated_at=? WHERE id=? AND status=?",
                (DeliveryStatus.PENDING.value, now, now, delivery_id, DeliveryStatus.AWAITING_APPROVAL.value),
            )
            return cur.rowcount == 1

    def cancel_delivery(self, delivery_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE deliveries SET status=?,lease_expires_at=NULL,leased_by=NULL,updated_at=?
                WHERE id=? AND status IN (?,?,?,?)""",
                (
                    DeliveryStatus.CANCELLED.value,
                    utcnow().isoformat(),
                    delivery_id,
                    DeliveryStatus.AWAITING_APPROVAL.value,
                    DeliveryStatus.PENDING.value,
                    DeliveryStatus.RETRY.value,
                    DeliveryStatus.LEASED.value,
                ),
            )
            return cur.rowcount == 1

    def lease_next(
        self,
        installation_id: str,
        lease_seconds: int,
        eligible_roles: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> Delivery | None:
        now = utcnow()
        lease_expiry = now + timedelta(seconds=lease_seconds)
        roles = sorted({str(role) for role in eligible_roles or []}) if eligible_roles is not None else None
        if roles == []:
            return None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            params: list[Any] = [DeliveryStatus.PENDING.value, DeliveryStatus.RETRY.value, now.isoformat()]
            role_clause = ""
            if roles is not None:
                role_clause = f" AND target_role IN ({','.join('?' for _ in roles)})"
                params.extend(roles)
            row = conn.execute(
                f"SELECT * FROM deliveries WHERE status IN (?,?) AND available_at<=?{role_clause} ORDER BY id LIMIT 1",
                params,
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """UPDATE deliveries SET status=?,attempt_count=attempt_count+1,lease_expires_at=?,leased_by=?,updated_at=? WHERE id=?""",
                (DeliveryStatus.LEASED.value, lease_expiry.isoformat(), installation_id, now.isoformat(), row["id"]),
            )
            conn.execute("COMMIT")
        return Delivery(
            id=row["id"],
            event_id=row["event_id"],
            target_role=row["target_role"],
            body=row["body"],
            delivery_token=row["delivery_token"],
            required_apps=json.loads(row["required_apps_json"]),
            strict_apps=bool(row["strict_apps"]),
            status=DeliveryStatus.LEASED,
            attempt_count=int(row["attempt_count"]) + 1,
            available_at=now,
            lease_expires_at=lease_expiry,
        )

    def complete_delivery(
        self,
        delivery_id: int,
        success: bool,
        code: str,
        detail: str | None,
        maximum_attempts: int,
        retry_delays: list[int],
        *,
        retryable: bool | None = None,
        observed_url: str | None = None,
        observed_message_marker: str | None = None,
    ) -> str:
        now = utcnow()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if not row:
                raise KeyError(delivery_id)
            if row["status"] != DeliveryStatus.LEASED.value:
                raise ValueError(f"delivery {delivery_id} is not leased")
            should_retry = retryable is not False
            if success:
                status = DeliveryStatus.DELIVERED.value
                available_at = now
            elif not should_retry or int(row["attempt_count"]) >= maximum_attempts:
                status = DeliveryStatus.FAILED.value
                available_at = now
            else:
                status = DeliveryStatus.RETRY.value
                index = min(max(int(row["attempt_count"]) - 1, 0), len(retry_delays) - 1)
                available_at = now + timedelta(seconds=retry_delays[index])
            conn.execute(
                """UPDATE deliveries SET status=?,available_at=?,lease_expires_at=NULL,leased_by=NULL,
                last_code=?,last_detail=?,observed_url=?,observed_message_marker=?,updated_at=? WHERE id=?""",
                (status, available_at.isoformat(), code, detail, observed_url, observed_message_marker, now.isoformat(), delivery_id),
            )
        return status

    def recover_expired_leases(self) -> int:
        now = utcnow().isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE deliveries SET status=?,leased_by=NULL,lease_expires_at=NULL,available_at=?,updated_at=?
                WHERE status=? AND lease_expires_at<?""",
                (DeliveryStatus.RETRY.value, now, now, DeliveryStatus.LEASED.value, now),
            )
            return cur.rowcount

    def update_task_state(self, task_id: str, state: str, event_id: str, worker_role: str | None, pr_number: int, sha: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO task_state(task_id,state,last_event_id,worker_role,pr_number,sha,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,last_event_id=excluded.last_event_id,
                worker_role=COALESCE(excluded.worker_role,task_state.worker_role),pr_number=excluded.pr_number,
                sha=COALESCE(excluded.sha,task_state.sha),updated_at=excluded.updated_at""",
                (task_id, state, event_id, worker_role, pr_number, sha, utcnow().isoformat()),
            )

    def task_state(self, task_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM task_state WHERE task_id=?", (task_id,)).fetchone()

    def record_heartbeat(self, installation_id: str, version: str, payload_json: str) -> None:
        now = utcnow().isoformat()
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        bindings = payload.get("bindings") if isinstance(payload, dict) else {}
        if not isinstance(bindings, dict):
            bindings = {}
        active_roles = set(payload.get("active_roles") or []) if isinstance(payload, dict) else set()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO heartbeats(installation_id,extension_version,payload_json,last_seen) VALUES(?,?,?,?)
                ON CONFLICT(installation_id) DO UPDATE SET extension_version=excluded.extension_version,
                payload_json=excluded.payload_json,last_seen=excluded.last_seen""",
                (installation_id, version, payload_json, now),
            )
            conn.execute("DELETE FROM role_endpoints WHERE installation_id=?", (installation_id,))
            for role, binding in bindings.items():
                if not isinstance(binding, dict):
                    if isinstance(binding, str) and binding:
                        binding = {"url": binding}
                    else:
                        continue
                url = str(binding.get("url") or "").strip()
                if not url:
                    continue
                conn.execute(
                    """INSERT INTO role_endpoints(installation_id,role,url,conversation_key,tab_id,active,composer_ready,
                    login_required,busy,extension_version,payload_json,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        installation_id,
                        str(role),
                        url,
                        binding.get("conversation_key"),
                        binding.get("tab_id"),
                        int(str(role) in active_roles),
                        int(bool(binding.get("composer_ready"))),
                        int(bool(binding.get("login_required"))),
                        int(bool(binding.get("busy"))),
                        version,
                        json.dumps(binding, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            conn.execute("COMMIT")

    def heartbeat_for_installation(self, installation_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM heartbeats WHERE installation_id=?",
                (installation_id,),
            ).fetchone()

    def bound_roles_for_installation(self, installation_id: str, stale_seconds: int) -> set[str]:
        cutoff = (utcnow() - timedelta(seconds=stale_seconds)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role FROM role_endpoints WHERE installation_id=? AND last_seen>=?",
                (installation_id, cutoff),
            ).fetchall()
        return {str(row["role"]) for row in rows}

    def fresh_role_endpoints(self, stale_seconds: int) -> list[dict[str, Any]]:
        cutoff = (utcnow() - timedelta(seconds=stale_seconds)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM role_endpoints WHERE last_seen>=? ORDER BY role,last_seen DESC",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_alert(self, severity: str, code: str, detail: str, task_id: str | None = None, pr_number: int | None = None, github_comment_url: str | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO alerts(severity,code,task_id,pr_number,detail,github_comment_url,created_at) VALUES(?,?,?,?,?,?,?)",
                (severity, code, task_id, pr_number, detail, github_comment_url, utcnow().isoformat()),
            )
            return int(cur.lastrowid)

    def resolve_alerts(self, *, code: str | None = None, task_id: str | None = None, pr_number: int | None = None) -> int:
        clauses = ["resolved_at IS NULL"]
        values: list[Any] = []
        if code is not None:
            clauses.append("code=?")
            values.append(code)
        if task_id is not None:
            clauses.append("task_id=?")
            values.append(task_id)
        if pr_number is not None:
            clauses.append("pr_number=?")
            values.append(pr_number)
        values.append(utcnow().isoformat())
        sql = f"UPDATE alerts SET resolved_at=? WHERE {' AND '.join(clauses)}"
        # resolved timestamp is first positional parameter.
        with self.connect() as conn:
            cur = conn.execute(sql, [values[-1], *values[:-1]])
            return cur.rowcount

    def resolve_alert(self, alert_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("UPDATE alerts SET resolved_at=? WHERE id=? AND resolved_at IS NULL", (utcnow().isoformat(), alert_id))
            return cur.rowcount == 1

    def latest_heartbeat(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM heartbeats ORDER BY last_seen DESC LIMIT 1").fetchone()

    def pending_delivery_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM deliveries WHERE status IN (?,?,?,?)",
                (DeliveryStatus.PENDING.value, DeliveryStatus.AWAITING_APPROVAL.value, DeliveryStatus.LEASED.value, DeliveryStatus.RETRY.value),
            ).fetchone()
        return int(row["n"])

    def open_alert_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE resolved_at IS NULL").fetchone()
        return int(row["n"])

    def latest_open_alert(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def has_recent_alert(self, code: str, within_seconds: int, task_id: str | None = None, pr_number: int | None = None) -> bool:
        cutoff = (utcnow() - timedelta(seconds=within_seconds)).isoformat()
        clauses = ["code=?", "created_at>=?", "resolved_at IS NULL"]
        values: list[Any] = [code, cutoff]
        if task_id is not None:
            clauses.append("task_id=?")
            values.append(task_id)
        if pr_number is not None:
            clauses.append("pr_number=?")
            values.append(pr_number)
        with self.connect() as conn:
            row = conn.execute(f"SELECT 1 FROM alerts WHERE {' AND '.join(clauses)} LIMIT 1", values).fetchone()
        return bool(row)


    def next_event_attempt(self, task_id: str, event_type: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE task_id=? AND event_type=?",
                (task_id, event_type),
            ).fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM decision_outbox WHERE task_id=? AND decision=?",
                (task_id, event_type.removeprefix('SAT2_')),
            ).fetchone()
        return int(row["n"]) + int(pending["n"]) + 1

    def event_row(self, event_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()

    def event_payload(self, event_id: str) -> dict[str, Any] | None:
        row = self.event_row(event_id)
        if not row:
            return None
        try:
            return json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            return None

    def delivery_context(self, delivery_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT d.*,e.task_id,e.repository,e.pr_number,e.event_type,e.payload_json,
                t.state AS task_state,t.last_event_id,t.worker_role,t.sha AS task_sha
                FROM deliveries d JOIN events e ON e.event_id=d.event_id
                LEFT JOIN task_state t ON t.task_id=e.task_id WHERE d.id=?""",
                (delivery_id,),
            ).fetchone()
        return dict(row) if row else None

    def endpoint_for(self, installation_id: str, role: str, stale_seconds: int) -> dict[str, Any] | None:
        cutoff = (utcnow() - timedelta(seconds=stale_seconds)).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM role_endpoints WHERE installation_id=? AND role=? AND last_seen>=?""",
                (installation_id, role, cutoff),
            ).fetchone()
        return dict(row) if row else None

    def decision_exists_for_message(self, delivery_id: int, assistant_message_hash: str, decision: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM decision_outbox WHERE delivery_id=? AND assistant_message_hash=? AND decision=?""",
                (delivery_id, assistant_message_hash, decision),
            ).fetchone()

    def ack_outbox_for_delivery(self, delivery_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_outbox WHERE delivery_id=? AND decision='WORKER_ACK' ORDER BY id DESC LIMIT 1",
                (delivery_id,),
            ).fetchone()
        return dict(row) if row else None

    def ack_exists_for_delivery(self, delivery_id: int) -> bool:
        with self.connect() as conn:
            return bool(conn.execute(
                "SELECT 1 FROM decision_outbox WHERE delivery_id=? AND decision='WORKER_ACK' LIMIT 1",
                (delivery_id,),
            ).fetchone())

    def create_outbox(
        self,
        *,
        decision_key: str,
        delivery_id: int | None,
        task_id: str,
        actor_role: str,
        conversation_key: str | None,
        assistant_message_id: str | None,
        assistant_message_hash: str | None,
        decision: str,
        summary: str,
        event_id: str,
        event_marker: str,
        event_payload_json: str,
        comment_body: str,
        waiting_for_human: bool,
    ) -> tuple[bool, dict[str, Any]]:
        now = utcnow().isoformat()
        status = OutboxStatus.WAITING_FOR_HUMAN.value if waiting_for_human else OutboxStatus.PENDING.value
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT * FROM decision_outbox WHERE decision_key=? OR event_id=? OR event_marker=?",
                    (decision_key, event_id, event_marker),
                ).fetchone()
                if existing:
                    conn.execute("COMMIT")
                    return False, dict(existing)
                cur = conn.execute(
                    """INSERT INTO decision_outbox(
                    decision_key,delivery_id,task_id,actor_role,conversation_key,assistant_message_id,
                    assistant_message_hash,decision,summary,event_id,event_marker,event_payload_json,
                    comment_body,status,attempt_count,available_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        decision_key, delivery_id, task_id, actor_role, conversation_key,
                        assistant_message_id, assistant_message_hash, decision, summary, event_id,
                        event_marker, event_payload_json, comment_body, status, 0, now, now, now,
                    ),
                )
                row = conn.execute("SELECT * FROM decision_outbox WHERE id=?", (cur.lastrowid,)).fetchone()
                conn.execute("COMMIT")
                return True, dict(row)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def outbox_row(self, outbox_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM decision_outbox WHERE id=?", (outbox_id,)).fetchone()
        return dict(row) if row else None

    def ready_outbox(self, limit: int = 20) -> list[dict[str, Any]]:
        now = utcnow().isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM decision_outbox WHERE status IN (?,?,?) AND available_at<=?
                ORDER BY id LIMIT ?""",
                (
                    OutboxStatus.PENDING.value,
                    OutboxStatus.RETRY.value,
                    OutboxStatus.PUBLISH_UNCERTAIN.value,
                    now,
                    limit,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def confirm_outbox(self, outbox_id: int) -> bool:
        now = utcnow().isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE decision_outbox SET status=?,available_at=?,updated_at=?
                WHERE id=? AND status=?""",
                (OutboxStatus.PENDING.value, now, now, outbox_id, OutboxStatus.WAITING_FOR_HUMAN.value),
            )
            return cur.rowcount == 1

    def mark_outbox_publishing(self, outbox_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE decision_outbox SET status=?,attempt_count=attempt_count+1,updated_at=?
                WHERE id=? AND status IN (?,?,?)""",
                (
                    OutboxStatus.PUBLISHING.value,
                    utcnow().isoformat(),
                    outbox_id,
                    OutboxStatus.PENDING.value,
                    OutboxStatus.RETRY.value,
                    OutboxStatus.PUBLISH_UNCERTAIN.value,
                ),
            )
            return cur.rowcount == 1

    def mark_outbox_published(self, outbox_id: int, comment_id: int, comment_url: str | None) -> None:
        now = utcnow().isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM decision_outbox WHERE id=?", (outbox_id,)).fetchone()
                if not row:
                    raise KeyError(outbox_id)
                conn.execute(
                    """UPDATE decision_outbox SET status=?,github_comment_id=?,github_comment_url=?,
                    last_error_code=NULL,last_error_detail=NULL,updated_at=? WHERE id=?""",
                    (OutboxStatus.PUBLISHED.value, comment_id, comment_url, now, outbox_id),
                )
                if row["delivery_id"] and row["decision"] != "WORKER_ACK":
                    conn.execute(
                        "UPDATE deliveries SET decision_consumed_at=?,updated_at=? WHERE id=?",
                        (now, now, row["delivery_id"]),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def mark_outbox_error(self, outbox_id: int, status: OutboxStatus, code: str, detail: str, retry_seconds: int = 10) -> None:
        available = (utcnow() + timedelta(seconds=max(1, retry_seconds))).isoformat()
        with self.connect() as conn:
            conn.execute(
                """UPDATE decision_outbox SET status=?,available_at=?,last_error_code=?,last_error_detail=?,updated_at=? WHERE id=?""",
                (status.value, available, code, detail, utcnow().isoformat(), outbox_id),
            )

    def pending_outbox_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM decision_outbox WHERE status NOT IN (?,?,?)",
                (OutboxStatus.PUBLISHED.value, OutboxStatus.BLOCKED.value, OutboxStatus.CANCELLED.value),
            ).fetchone()
        return int(row["n"])

    def status_snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            deliveries = [dict(r) for r in conn.execute("SELECT * FROM deliveries ORDER BY id DESC LIMIT 200")]
            tasks = [dict(r) for r in conn.execute("SELECT * FROM task_state ORDER BY task_id")]
            outbox = [dict(r) for r in conn.execute("SELECT * FROM decision_outbox ORDER BY id DESC LIMIT 200")]
            heartbeats = [dict(r) for r in conn.execute("SELECT * FROM heartbeats ORDER BY last_seen DESC")]
            endpoints = [dict(r) for r in conn.execute("SELECT * FROM role_endpoints ORDER BY role,last_seen DESC")]
            alerts = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 200")]
            comments = [dict(r) for r in conn.execute("SELECT * FROM comments ORDER BY processed_at DESC LIMIT 100")]
        return {
            "meta": self.all_meta(),
            "deliveries": deliveries,
            "tasks": tasks,
            "outbox": outbox,
            "heartbeats": heartbeats,
            "role_endpoints": endpoints,
            "alerts": alerts,
            "comments": comments,
        }
