"""Explicit notary schema migration (Issue #343): the only code that upgrades an existing notary.

Opening a notary never migrates it: an older schema is refused with NOTARY_SCHEMA_OLDER, so a
newer deploy cannot silently make a state unreadable for the older deploy it is pinned to. The
operator runs ``cairnwork-verify --state DIR migrate-notary``, which copies the database to a
backup beside it and then applies the ALTERs, all under one write lock.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cairnwork.intake_request import IntakeRefused
from cairnwork.notary import Notary, NotaryError, NotarySchemaMismatch
from cairnwork.notary_schema import MIGRATIONS, SCHEMA_VERSION


class NotaryMissing(NotaryError):
    code = "NOTARY_MISSING"


def refuse_unmigrated(path: Path) -> None:
    """IntakeRefused (NOTARY_SCHEMA_OLDER or NOTARY_SCHEMA_MISMATCH) for an existing notary a run
    could not open, so submit and check refuse before anything is bound (#343)."""
    path = Path(path)
    if not path.is_file():  # a missing notary is created by the run, as before
        return
    try:
        Notary(path, core_verify=_never_called)
    except NotarySchemaMismatch as exc:
        raise IntakeRefused(exc.code, str(exc).removeprefix(f"{exc.code}: ")) from exc


def _never_called(_: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    raise AssertionError("opening a notary never calls Core")


def migrate_notary(path: Path) -> dict[str, Any]:
    """Back up, then migrate the notary at path to SCHEMA_VERSION. A current notary is left
    untouched (no backup); a missing one is not created; an unknown version is refused. The
    caller checks the state directory and holds the exclusive run lock (verify_cli)."""
    path = Path(path)
    if not path.is_file():  # sqlite3.connect would create an empty database
        raise NotaryMissing(f"no notary at {path}")
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")  # no writer can commit between the backup and the ALTERs
        try:
            result = _migrate_locked(conn, path)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return result


def _migrate_locked(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    old = conn.execute("PRAGMA user_version").fetchone()[0]
    if old == SCHEMA_VERSION:
        return {"migrated": False, "from": old, "to": SCHEMA_VERSION}
    if old not in MIGRATIONS:
        raise NotarySchemaMismatch(f"user_version {old}, need {SCHEMA_VERSION}")
    backup = _backup(path, old)
    version = old
    while version in MIGRATIONS:
        conn.execute(MIGRATIONS[version])
        version += 1
    if version != SCHEMA_VERSION:
        raise NotarySchemaMismatch(f"user_version {version}, need {SCHEMA_VERSION}")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return {"migrated": True, "from": old, "to": SCHEMA_VERSION, "backup": str(backup)}


def _backup(path: Path, version: int) -> Path:
    """A consistent copy of the database, read through a second connection: the caller's write
    lock (RESERVED, nothing changed yet) still lets it read, and keeps every writer out until the
    ALTERs commit. Written to a new .tmp file and linked to its final name only when complete,
    so a .bak is never partial; an existing name is refused."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.v{version}-{stamp}.bak")
    partial = target.with_name(target.name + ".tmp")
    os.close(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    try:
        source, copy = sqlite3.connect(path, timeout=30), sqlite3.connect(partial)
        try:  # not the locking connection: SQLite refuses a backup from a writing connection
            source.backup(copy)
        finally:
            source.close()
            copy.close()
        os.link(partial, target)  # fails if the name exists; never replaces a file
    finally:
        partial.unlink(missing_ok=True)
    return target
