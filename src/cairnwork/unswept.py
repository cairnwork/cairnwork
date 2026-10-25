"""Attempts whose processes are not shown gone (#244 window 1): the ledger's takeover fence.

A FAILED task is marked when its attempt's processes may still run: recovery could not read the
process table, or what it killed was not shown gone within the confirm window (recovery.py), or
the live run's worker could not show them gone (``WorkerMayLive``, e.g. agy's
WORKER_SWEEP_UNCLEAN; pipeline.py). The mark names the attempt and the worker account it ran as
("" for the owner's uid). While it is there the ledger refuses SUPERSEDED, the only way to
release the card for a new attempt, so two attempts' effects cannot overlap. takeover.py sweeps
again and, only when the processes are shown gone, supersedes and drops the mark in one
transaction; ``refusal`` keeps the last reason a takeover was refused, for ``status``.

The mark is written inside the ledger transaction that moves the task to FAILED
(``OwnershipLedger.set_status(..., fence=...)``), so there is no FAILED task without its mark.

Ledgers from before this table (migrate): the ledger keeps no failure reasons, so it cannot tell
which FAILED task recovery left unswept, and a runner of the old version may still end an
in-flight task FAILED without a mark. Every FAILED, BOUND or DISPATCHED task is marked LEGACY
(attempt "") when the table is created, in that same transaction; takeover.py then sweeps every
attempt the journal names for the task. A task the journal names no attempt for stays fenced.

Journal entries from before #244 do not name the account a worker ran as (WORKER_STARTED had no
detail): ``journaled_account`` reads them as UNKNOWN_ACCOUNT, never as the owner's uid.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

LEGACY = ""  # attempt id of a mark made at the migration: the ledger does not know the attempt
UNKNOWN_ACCOUNT = "?"  # the account of an attempt journaled before #244 (no user is named "?")
RUN_AS = "run_as="  # WORKER_STARTED detail: "run_as=<worker account>", "run_as=" for the owner

SCHEMA = """
CREATE TABLE unswept_attempt (
    task_id    TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    account    TEXT NOT NULL DEFAULT '',
    refusal    TEXT NOT NULL DEFAULT ''
)
"""


class WorkerMayLive(Exception):
    """A worker that could not show its processes gone; ``account`` is the worker account they
    run as ("" for the owner's uid). The run's FAILED move marks the attempt (pipeline.py)."""

    def __init__(self, message: str = "", account: str = "") -> None:
        super().__init__(message)
        self.account = account


@dataclass(frozen=True)
class Mark:
    attempt_id: str  # LEGACY when the mark was made at the migration
    account: str  # the worker account the attempt ran as; "" for the owner's uid
    refusal: str  # the last reason a takeover was refused; "" if none yet


def migrate(conn: sqlite3.Connection) -> None:
    """Create the table once; a ledger that predates it gets every FAILED, BOUND or DISPATCHED
    task marked LEGACY."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'unswept_attempt'"
        ).fetchone()
        if exists is None:
            conn.execute(SCHEMA)
            conn.execute(  # in flight too: an old runner may still end it FAILED, unmarked
                "INSERT INTO unswept_attempt (task_id, attempt_id) SELECT task_id, ?"
                " FROM task_ownership WHERE status IN ('BOUND', 'DISPATCHED', 'FAILED')",
                (LEGACY,),
            )
        conn.execute("COMMIT")
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def journaled_account(entries) -> str | None:
    """The account an attempt's WORKER_STARTED entry names: "" for the owner's uid,
    UNKNOWN_ACCOUNT for an entry from before #244 (it named none), None if no worker started."""
    details = [e.detail for e in entries if e.phase == "WORKER_STARTED"]
    if not details:
        return None
    return details[-1].removeprefix(RUN_AS) if details[-1].startswith(RUN_AS) else UNKNOWN_ACCOUNT


def get(conn: sqlite3.Connection, task_id: str) -> Mark | None:
    row = conn.execute(
        "SELECT attempt_id, account, refusal FROM unswept_attempt WHERE task_id = ?", (task_id,)
    ).fetchone()
    return Mark(*row) if row else None


def mark(conn: sqlite3.Connection, task_id: str, attempt_id: str, account: str) -> None:
    """A second mark of one task names the newest attempt: a task has one dispatched attempt
    (journal.py), so a re-mark is a later, better reading of it, and the takeover must sweep the
    attempt the FAILED status is about; the old refusal no longer applies."""
    conn.execute(
        "INSERT INTO unswept_attempt (task_id, attempt_id, account) VALUES (?, ?, ?)"
        " ON CONFLICT (task_id) DO UPDATE SET attempt_id = excluded.attempt_id,"
        " account = excluded.account, refusal = ''",
        (task_id, attempt_id, account),
    )


def note(conn: sqlite3.Connection, task_id: str, refusal: str) -> None:
    conn.execute("UPDATE unswept_attempt SET refusal = ? WHERE task_id = ?", (refusal, task_id))


def clear(conn: sqlite3.Connection, task_id: str) -> None:
    conn.execute("DELETE FROM unswept_attempt WHERE task_id = ?", (task_id,))
