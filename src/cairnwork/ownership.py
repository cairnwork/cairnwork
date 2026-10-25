"""Durable task ownership ledger (preserved behaviors #6 and #7).

Every task is bound to exactly one owning system before dispatch. The binding is one atomic
SQLite transaction keyed by idempotency key, and it carries the request digest, the card hash,
the owner and the task id. Resend, query, cancel and recovery route by this record; when no
record exists the caller must stop, never guess.

At most one task per card is active. CANCELLED (BOUND only) and SUPERSEDED release the card;
COMPLETED and FAILED keep holding it. A resend with the same key returns the same record and
never re-runs it (a FAILED task stays FAILED); to retry, mark it SUPERSEDED, then bind a new key.
Resending an identical request (same digest) under another key or card is refused while a task
for it is active
(REPLAY_FORBIDDEN if COMPLETED or FAILED, RECONCILIATION_REQUIRED if BOUND or
DISPATCHED). The one-active-card rule lives in the schema (a partial unique index), so it
also holds for raw writers; ``bind`` maps the index violation to DuplicateLogicalTask.

A FAILED task whose attempt's processes are not shown gone (unswept.py) cannot be SUPERSEDED:
``set_status`` raises TakeoverRefused until ``takeover.supersede`` has shown them gone.

The ledger must live on a local disk: it uses SQLite WAL, which is unsafe on network
filesystems, and opening fails if WAL cannot be enabled.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cairnwork import unswept

OWNER = "cairnwork"  # the one identity this system binds, runs and cancels as
OWNERS = frozenset({OWNER, "nexus-new"})
INACTIVE_STATUSES = frozenset({"SUPERSEDED", "CANCELLED"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "BOUND": frozenset({"DISPATCHED", "FAILED", "CANCELLED", "SUPERSEDED"}),
    "DISPATCHED": frozenset({"COMPLETED", "FAILED"}),  # never CANCELLED: its run may live (#63)
    "FAILED": frozenset({"SUPERSEDED"}),
    "COMPLETED": frozenset(),
    "CANCELLED": frozenset(),
    "SUPERSEDED": frozenset(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_ownership (
    idempotency_key TEXT PRIMARY KEY,
    request_digest  TEXT NOT NULL,
    owner           TEXT NOT NULL,
    task_id         TEXT NOT NULL UNIQUE,
    card_hash       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
        ('BOUND', 'DISPATCHED', 'COMPLETED', 'FAILED', 'CANCELLED', 'SUPERSEDED'))
);
CREATE INDEX IF NOT EXISTS task_ownership_card ON task_ownership (card_hash);
CREATE UNIQUE INDEX IF NOT EXISTS task_ownership_one_active_per_card
    ON task_ownership (card_hash) WHERE status NOT IN ('SUPERSEDED', 'CANCELLED');
"""


class OwnershipError(Exception):
    """Base class; every subclass carries a stable machine-readable code."""

    code = "OWNERSHIP_ERROR"

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class KeyReusedWithDifferentRequest(OwnershipError):
    code = "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"


class ReplayForbidden(OwnershipError):
    code = "REPLAY_FORBIDDEN"


class ReconciliationRequired(OwnershipError):
    code = "RECONCILIATION_REQUIRED"


class OwnerMismatch(OwnershipError):
    code = "OWNER_MISMATCH"


class DuplicateLogicalTask(OwnershipError):
    code = "DUPLICATE_LOGICAL_TASK"


class OwnershipUnknown(OwnershipError):
    code = "OWNERSHIP_UNKNOWN"


class InvalidOwnershipValue(OwnershipError):
    code = "INVALID_OWNERSHIP_VALUE"


class InvalidStatusTransition(OwnershipError):
    code = "INVALID_STATUS_TRANSITION"


class TakeoverRefused(OwnershipError):
    """SUPERSEDED refused: the FAILED attempt's processes are not shown gone (takeover.py)."""

    code = "TAKEOVER_REFUSED"


class LedgerStorageUnsupported(OwnershipError):
    code = "LEDGER_STORAGE_UNSUPPORTED"


class LedgerIntegrityViolation(OwnershipError):
    code = "LEDGER_INTEGRITY_VIOLATION"


@dataclass(frozen=True)
class OwnershipRecord:
    idempotency_key: str
    request_digest: str
    owner: str
    task_id: str
    card_hash: str
    status: str


@dataclass(frozen=True)
class BindResult:
    record: OwnershipRecord
    duplicate: bool


def request_digest(request: dict[str, Any]) -> str:
    """sha256 over canonical JSON; key order and whitespace do not matter.

    Only JSON-native values with string keys are accepted, so two different
    requests cannot collapse into one digest (``{1: x}`` vs ``{"1": x}``), and
    NaN or infinity are rejected.
    """
    if not isinstance(request, dict):
        raise InvalidOwnershipValue("request must be a dict")
    _check_json_value(request, "request")
    canonical = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OwnershipLedger:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        conn = self._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise LedgerStorageUnsupported(f"journal_mode is {mode!r}, need wal")
            try:
                conn.executescript(_SCHEMA)
                unswept.migrate(conn)  # after task_ownership: marks a pre-#244 ledger's FAILED
            except sqlite3.IntegrityError as exc:
                raise LedgerIntegrityViolation(str(exc)) from exc
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def bind(
        self, idempotency_key: str, request: dict[str, Any], owner: str, card_hash: str
    ) -> BindResult:
        """Bind a new task, or return the existing binding for an identical resend."""
        _require_token(idempotency_key, "idempotency_key")
        _require_token(card_hash, "card_hash")
        if owner not in OWNERS:
            raise InvalidOwnershipValue(f"owner {owner!r}")
        digest = request_digest(request)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = _select(conn, "idempotency_key", idempotency_key)
            if existing is not None:
                conn.execute("ROLLBACK")
                return BindResult(_same_binding(existing, digest, owner, card_hash), True)
            _refuse_replay(conn, digest, card_hash)
            record = OwnershipRecord(
                idempotency_key, digest, owner, f"task-{uuid.uuid4().hex}", card_hash, "BOUND"
            )
            _insert(conn, record)
            conn.execute("COMMIT")
            return BindResult(record, duplicate=False)
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    def route(self, *, task_id: str | None = None, idempotency_key: str | None = None) -> str:
        """Return the owning system; raise OwnershipUnknown instead of guessing."""
        return self.get(task_id=task_id, idempotency_key=idempotency_key).owner

    def get(
        self, *, task_id: str | None = None, idempotency_key: str | None = None
    ) -> OwnershipRecord:
        if (task_id is None) == (idempotency_key is None):
            raise InvalidOwnershipValue("give exactly one of task_id or idempotency_key")
        column, value = (
            ("task_id", task_id) if task_id is not None else ("idempotency_key", idempotency_key)
        )
        conn = self._connect()
        try:
            record = _select(conn, column, value)
        finally:
            conn.close()
        if record is None:
            raise OwnershipUnknown(str(value))
        return record

    @property
    def path(self) -> Path:
        return self._path

    def in_flight(self, *, include_foreign: bool = False) -> list[OwnershipRecord]:
        """Our tasks bound or dispatched but not finished (what a crash can leave behind).

        Rows another system owns are not ours to wait on or settle, so quiescence counts
        only OWNER rows; include_foreign=True returns every row (recovery reports them).
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM task_ownership WHERE status IN ('BOUND', 'DISPATCHED')"
                " ORDER BY task_id"
            ).fetchall()
        finally:
            conn.close()
        records = [OwnershipRecord(*row) for row in rows]
        return records if include_foreign else [r for r in records if r.owner == OWNER]

    def foreign_in_flight(self) -> list[OwnershipRecord]:
        """Bound or dispatched rows another system owns (reported, never counted)."""
        return [r for r in self.in_flight(include_foreign=True) if r.owner != OWNER]

    def unswept_attempt(self, task_id: str) -> unswept.Mark | None:
        """The task's takeover fence (unswept.py), or None."""
        return self._on_conn(unswept.get, task_id)

    def note_refusal(self, task_id: str, refusal: str) -> None:
        self._on_conn(unswept.note, task_id, refusal)

    def _on_conn(self, operation, *args):
        conn = self._connect()
        try:
            return operation(conn, *args)
        finally:
            conn.close()

    def set_status(
        self,
        task_id: str,
        status: str,
        *,
        expected_from: str | None = None,
        fence: tuple[str, str] | None = None,
        swept: str | None = None,
        started_nothing: bool = False,
    ) -> OwnershipRecord:
        """Move a task along TRANSITIONS; the owner of a task can never change.
        expected_from: a compare-and-set in the same transaction (else InvalidStatusTransition).
        fence=(attempt id, account), FAILED only: mark the attempt unswept in that transaction.
        SUPERSEDED of a marked task needs ``swept`` = the marked attempt (takeover.supersede
        showed its processes gone); the mark goes with the move, as a LEGACY one does with
        ``started_nothing`` (recovery of a task still BOUND, so nothing was spawned)."""
        if status not in TRANSITIONS or (fence is not None and status != "FAILED"):
            raise InvalidOwnershipValue(f"status {status!r}" + " with a fence" * bool(fence))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = _select(conn, "task_id", task_id)
            if current is None:
                raise OwnershipUnknown(task_id)
            if expected_from is not None and current.status != expected_from:
                raise InvalidStatusTransition(f"{current.status} is not {expected_from}")
            if status not in TRANSITIONS.get(current.status, frozenset()):
                raise InvalidStatusTransition(f"{current.status} -> {status}")
            if status == "SUPERSEDED" and (held := unswept.get(conn, task_id)) is not None:
                if swept != held.attempt_id:
                    why = f"attempt {held.attempt_id or 'LEGACY'} is not shown gone (takeover.py)"
                    raise TakeoverRefused(f"{task_id}: UNSWEPT_ATTEMPT: {why}")
                unswept.clear(conn, task_id)
            if fence is not None:
                unswept.mark(conn, task_id, *fence)
            if started_nothing and current.status == "BOUND":
                unswept.clear_legacy(conn, task_id)
            conn.execute(
                "UPDATE task_ownership SET status = ? WHERE task_id = ?", (status, task_id)
            )
            conn.execute("COMMIT")
            return replace(current, status=status)
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()


def _same_binding(
    existing: OwnershipRecord, digest: str, owner: str, card_hash: str
) -> OwnershipRecord:
    if existing.request_digest != digest or existing.card_hash != card_hash:
        raise KeyReusedWithDifferentRequest(existing.idempotency_key)
    if existing.owner != owner:
        raise OwnerMismatch(f"{existing.task_id} is owned by {existing.owner}, not {owner}")
    return existing


def _refuse_replay(conn: sqlite3.Connection, digest: str, card_hash: str) -> None:
    """The same request must not run twice under another key or card.

    Checked in code, inside bind's transaction, not by a unique index: a state
    directory that already holds two rows of one digest must still open. A task
    on the same card is left to DuplicateLogicalTask (the card index).
    """
    row = conn.execute(
        "SELECT task_id, status FROM task_ownership WHERE request_digest = ?"
        " AND card_hash != ? AND status NOT IN ('SUPERSEDED', 'CANCELLED')"
        " ORDER BY status IN ('COMPLETED', 'FAILED') DESC",
        (digest, card_hash),
    ).fetchone()
    if row is None:
        return
    task_id, status = row
    if status in ("COMPLETED", "FAILED"):
        hint = "supersede it, then use a new key" if status == "FAILED" else "it is not resent"
        raise ReplayForbidden(f"{task_id} is {status}; {hint}")
    raise ReconciliationRequired(f"{task_id} is {status}")


def _insert(conn: sqlite3.Connection, record: OwnershipRecord) -> None:
    try:
        conn.execute(
            "INSERT INTO task_ownership VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.idempotency_key,
                record.request_digest,
                record.owner,
                record.task_id,
                record.card_hash,
                record.status,
            ),
        )
    except sqlite3.IntegrityError as exc:
        active = _active_for_card(conn, record.card_hash)
        if active is None:
            raise
        raise DuplicateLogicalTask(
            f"card {record.card_hash} owned by task {active.task_id}"
        ) from exc


def _require_token(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidOwnershipValue(f"{name} must be a non-empty string without outer spaces")


def _check_json_value(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidOwnershipValue(f"{where}: key {key!r} is not a string")
            _check_json_value(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_json_value(item, f"{where}[{index}]")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidOwnershipValue(f"{where}: {value!r} is not finite")
    elif value is not None and not isinstance(value, str | int | bool):
        raise InvalidOwnershipValue(f"{where}: {type(value).__name__} is not JSON")


def _select(conn: sqlite3.Connection, column: str, value: Any) -> OwnershipRecord | None:
    if column not in {"idempotency_key", "task_id"}:
        raise InvalidOwnershipValue(f"column {column!r}")
    row = conn.execute(
        f"SELECT * FROM task_ownership WHERE {column} = ?",  # noqa: S608 - column is whitelisted
        (value,),
    ).fetchone()
    return OwnershipRecord(*row) if row else None


def _active_for_card(conn: sqlite3.Connection, card_hash: str) -> OwnershipRecord | None:
    row = conn.execute(
        "SELECT * FROM task_ownership WHERE card_hash = ?"
        " AND status NOT IN ('SUPERSEDED', 'CANCELLED')",
        (card_hash,),
    ).fetchone()
    return OwnershipRecord(*row) if row else None
