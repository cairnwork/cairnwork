"""Attempts whose processes were never checked (#244): the ledger's takeover fence.

Recovery (recovery.py) marks the task it settles FAILED when the process sweep was unavailable
(``SWEEP_UNAVAILABLE``): the dead attempt's worker may still be running. While the mark is
there the ledger refuses SUPERSEDED, the only way to release the card for a new attempt, so two
attempts' effects cannot overlap. takeover.py re-runs the sweep and clears the mark; ``refusal``
keeps the last reason a takeover was refused, for ``status``. Plain SQL on the ledger's own
connection, so the mark and the status move share one transaction.
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS unswept_attempt (
    task_id    TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    refusal    TEXT NOT NULL DEFAULT ''
);
"""


def get(conn: sqlite3.Connection, task_id: str) -> tuple[str, str] | None:
    """(attempt_id, last refusal) of the unchecked attempt, or None."""
    row = conn.execute(
        "SELECT attempt_id, refusal FROM unswept_attempt WHERE task_id = ?", (task_id,)
    ).fetchone()
    return (row[0], row[1]) if row else None


def mark(conn: sqlite3.Connection, task_id: str, attempt_id: str) -> None:
    conn.execute(
        "INSERT INTO unswept_attempt (task_id, attempt_id) VALUES (?, ?)"
        " ON CONFLICT (task_id) DO UPDATE SET attempt_id = excluded.attempt_id",
        (task_id, attempt_id),
    )


def note(conn: sqlite3.Connection, task_id: str, refusal: str) -> None:
    conn.execute("UPDATE unswept_attempt SET refusal = ? WHERE task_id = ?", (refusal, task_id))


def clear(conn: sqlite3.Connection, task_id: str) -> None:
    conn.execute("DELETE FROM unswept_attempt WHERE task_id = ?", (task_id,))
