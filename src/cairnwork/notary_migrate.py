"""Explicit notary schema migration (Issue #343): the only code that upgrades an existing notary.

Opening a notary never migrates it: an older schema is refused with NOTARY_SCHEMA_OLDER, so a
newer deploy cannot silently make a state unreadable for the older deploy it is pinned to. The
operator runs ``cairnwork-verify --state DIR migrate-notary``, which copies the database to a
backup beside it and then applies the ALTERs, all under one write lock.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cairnwork.intake_request import IntakeRefused
from cairnwork.notary import NotaryError, NotarySchemaMismatch, NotarySchemaOlder, schema_refusal
from cairnwork.notary_schema import MIGRATIONS, SCHEMA_VERSION


class NotaryMissing(NotaryError):
    code = "NOTARY_MISSING"


def _probe(path: Path) -> tuple[int, bool, bool] | None:
    """None when there is no notary yet (no file, or zero bytes); else (user_version, has the
    notary table, has any schema object). Never writes to path or the files beside it (#367): a
    read-only open, or a read of a private copy when SQLite would roll back a hot journal."""
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:  # e.g. a directory on the way we may not search
        raise IntakeRefused("BAD_STATE", f"cannot read {path}: {exc.strerror}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise IntakeRefused("BAD_STATE", f"{path} is not a regular file")
    if info.st_size == 0:
        return None
    path = path.resolve()  # a symlink: SQLite keeps -journal, -wal and -shm beside the real file
    try:
        with path.open("rb") as file:
            header = file.read(20)
    except OSError as exc:  # e.g. mode 0000
        raise IntakeRefused("BAD_STATE", f"cannot read {path}: {exc.strerror}") from exc
    if b"\x02" in header[18:] or path.with_name(path.name + "-wal").exists():
        # A notary is never in WAL mode, and even a read-only open would write its -shm
        raise NotarySchemaMismatch(f"{path} is in WAL mode, which no notary is")
    try:
        try:
            return _read_schema(path, "ro")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorname != "SQLITE_READONLY_ROLLBACK":
                raise
            return _read_copy(path)  # a hot journal: rolling it back here would write
    except sqlite3.DatabaseError as exc:  # busy, for one, is not a fact about the file
        if exc.sqlite_errorname != "SQLITE_NOTADB":
            raise
        raise NotarySchemaMismatch(f"{path} is not a notary database: {exc}") from exc


def _read_copy(path: Path) -> tuple[int, bool, bool]:
    """The schema after SQLite rolls back the hot journal beside path, read from copies in a
    private directory: the original file and journal are left exactly as they are."""
    with tempfile.TemporaryDirectory() as tmp:  # mode 0700, removed afterwards
        copy = Path(tmp) / path.name
        for suffix in ("", "-journal"):
            shutil.copyfile(path.with_name(path.name + suffix), copy.with_name(copy.name + suffix))
        return _read_schema(copy, "rw")


def _read_schema(path: Path, mode: str) -> tuple[int, bool, bool]:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=30)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        objects = conn.execute("SELECT type, name FROM sqlite_master").fetchall()
    finally:
        conn.close()
    return version, ("table", "core_verifications") in objects, bool(objects)


def refuse_unmigrated(path: Path) -> None:
    """IntakeRefused (NOTARY_SCHEMA_OLDER or NOTARY_SCHEMA_MISMATCH) for an existing notary a run
    could not open, so submit and check refuse before anything is bound (#343). Never writes:
    decided from the probe, without opening the notary (#367)."""
    path = Path(path)
    try:
        found = _probe(path)
        problem = None if found is None else schema_refusal(path, *found)
    except NotarySchemaMismatch as exc:
        problem = exc
    if problem is not None:  # none: no notary yet, a blank database (the run creates it), current
        raise IntakeRefused(problem.code, str(problem).removeprefix(f"{problem.code}: "))


def migrate_notary(path: Path) -> dict[str, Any]:
    """Back up, then migrate the notary at path to SCHEMA_VERSION. A current notary is left
    untouched (no backup); a missing one is not created; an unknown version is refused. The
    caller checks the state directory and holds the exclusive run lock (verify_cli)."""
    path = Path(path)
    found = _probe(path)  # sqlite3.connect would create an empty database
    if found is None or found == (0, False, False):  # as refuse_unmigrated (#367)
        raise NotaryMissing(f"no notary at {path}")
    problem = schema_refusal(path, *found)
    if problem is not None and not isinstance(problem, NotarySchemaOlder):
        raise problem  # before anything opens the file to write, or a backup is written
    if not os.access(path, os.W_OK):  # e.g. 0444: the open below would fail as INTERNAL
        raise IntakeRefused("BAD_STATE", f"{path} is not writable")
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
