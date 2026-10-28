"""Issue #367: a notary refusal never writes, and a bad state path is a named refusal."""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from verify_helpers import cli

import cairnwork.notary_migrate as notary_migrate
from cairnwork.intake import Intake
from cairnwork.intake_request import IntakeRefused
from cairnwork.notary import Notary, NotarySchemaMismatch
from cairnwork.notary_migrate import refuse_unmigrated
from cairnwork.notary_schema import SCHEMA_VERSION

ROOT = os.geteuid() == 0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sqlite_file(path, version=0, other_table=False):
    conn = sqlite3.connect(path)
    if other_table:
        conn.execute("CREATE TABLE other (x)")
    conn.execute(f"PRAGMA user_version = {version}")  # writes page 1: a header, no notary table
    conn.commit()
    conn.close()


def route(state, *args, stdin=""):
    done = subprocess.run(
        [sys.executable, "-I", "-m", "cairnwork.route", "--state", str(state), *args],
        input=stdin, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    return done.returncode, json.loads(done.stdout)


def test_check_on_a_blank_sqlite_file_writes_nothing(state):
    path = state / "notary.sqlite"
    sqlite_file(path)
    before = digest(path)
    refuse_unmigrated(path)  # a blank database is no notary yet: the run creates it
    route(state, "check", stdin="{}")
    assert digest(path) == before


@pytest.mark.parametrize("kind", ["versioned", "other_table"])
def test_a_file_without_the_notary_table_is_refused_unwritten(state, kind):
    path = state / "notary.sqlite"
    sqlite_file(path, version=2 if kind == "versioned" else 0, other_table=kind == "other_table")
    before = digest(path)
    with pytest.raises(IntakeRefused) as caught:
        refuse_unmigrated(path)
    assert caught.value.code == "NOTARY_SCHEMA_MISMATCH"
    code, out = route(state, "check", stdin="{}")
    assert (code, out["error"]) == (2, "NOTARY_SCHEMA_MISMATCH"), out
    with pytest.raises(NotarySchemaMismatch):  # a run does not stamp the schema on it either
        Notary(path)
    assert digest(path) == before


NOT_A_DATABASE = [b"not a database\n" * 8, b"SQLite format 3\x00" + b"\x00" * 84]


@pytest.mark.parametrize("content", NOT_A_DATABASE, ids=["junk", "truncated_header"])
def test_a_file_that_is_no_database_is_a_named_refusal(state, content):
    path = state / "notary.sqlite"
    path.write_bytes(content)
    for code, out in (route(state, "check", stdin="{}"), cli(state, "migrate-notary")):
        assert (code, out["error"]) == (2, "NOTARY_SCHEMA_MISMATCH"), out
    assert path.read_bytes() == content


@pytest.mark.parametrize("blank", [False, True])
def test_migrate_notary_on_an_empty_notary_is_notary_missing(state, blank):
    path = state / "notary.sqlite"
    sqlite_file(path) if blank else path.touch()
    before = path.read_bytes()
    code, out = cli(state, "migrate-notary")
    assert (code, out["error"]) == (2, "NOTARY_MISSING"), out  # as refuse_unmigrated treats it
    assert path.read_bytes() == before and not list(state.glob("*.bak"))


def test_migrate_notary_without_the_table_is_refused_before_any_backup(state):
    path = state / "notary.sqlite"
    sqlite_file(path, version=2)
    before = digest(path)
    code, out = cli(state, "migrate-notary")
    assert (code, out["error"]) == (2, "NOTARY_SCHEMA_MISMATCH"), out
    assert digest(path) == before and not list(state.glob("*.bak"))


def test_a_state_whose_parent_is_a_file_is_bad_state(tmp_path):
    parent = tmp_path / "file"
    parent.write_text("x")
    code, out = cli(parent / "state", "migrate-notary")
    assert (code, out["error"]) == (2, "BAD_STATE"), out


@pytest.mark.skipif(ROOT, reason="root reads any directory")
@pytest.mark.parametrize("where", ["parent_000", "state_0600"])
def test_an_unsearchable_state_is_bad_state_and_the_notary_unchanged(tmp_path, where):
    state = tmp_path / "p" / "state"
    state.mkdir(parents=True, mode=0o700)
    Notary(state / "notary.sqlite")
    before = digest(state / "notary.sqlite")
    locked = state.parent if where == "parent_000" else state
    locked.chmod(0o000 if where == "parent_000" else 0o600)
    try:
        for code, out in (cli(state, "migrate-notary"), route(state, "check", stdin="{}")):
            assert (code, out["error"]) == (2, "BAD_STATE"), out
    finally:
        locked.chmod(0o700)
    assert digest(state / "notary.sqlite") == before


def test_a_failing_stat_of_the_notary_is_bad_state(state, monkeypatch):
    path = state / "notary.sqlite"
    Notary(path)
    real = Path.stat

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(IntakeRefused) as caught:
        refuse_unmigrated(path)
    assert caught.value.code == "BAD_STATE"


CRASH = """import os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1], isolation_level=None)
conn.execute("PRAGMA cache_size = 1")
conn.execute("BEGIN")
conn.execute("CREATE TABLE pad (x)")
conn.execute("INSERT INTO pad VALUES (randomblob(200000))")
os._exit(0)
"""
WAL = """import os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1], isolation_level=None)
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("CREATE TABLE other (x)")
os._exit(0)
"""


def crash(path, script=CRASH):
    """A writer that dies mid-transaction: its hot -journal (or its -wal) stays beside path."""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=60)


def files(path):
    """Size and digest of path and every -journal, -wal or -shm beside it."""
    return {p.name: (p.stat().st_size, digest(p)) for p in path.parent.glob(path.name + "*")}


def test_a_crashed_writers_journal_is_no_mismatch(state):
    """A hot journal makes a read-only open fail; refusing reads a copy, and the migration
    (or the run) rolls the notary back as any open would."""
    path = state / "notary.sqlite"
    Notary(path)
    crash(path)
    assert path.with_name(path.name + "-journal").stat().st_size > 0  # the crash left it hot
    before = files(path)
    refuse_unmigrated(path)
    assert files(path) == before
    assert cli(state, "migrate-notary") == (
        0,
        {"migrated": False, "from": SCHEMA_VERSION, "to": SCHEMA_VERSION},
    )


def foreign_with_journal(path):
    sqlite_file(path, other_table=True)
    crash(path)


def newer_notary_with_journal(path):
    Notary(path)
    sqlite_file(path, version=99)
    crash(path)


def garbage_with_journal(path):
    other = path.with_name("other.sqlite")
    foreign_with_journal(other)
    path.write_bytes(b"not a database\n" * 512)
    shutil.copyfile(other.with_name("other.sqlite-journal"), path.with_name(path.name + "-journal"))


def foreign_wal(path):
    crash(path, WAL)
    path.with_name(path.name + "-shm").unlink()  # a -wal alone: a read would make the -shm


def stray_wal(path):
    Notary(path)
    path.with_name(path.name + "-wal").write_bytes(b"\x00" * 32)


MAKERS = [foreign_with_journal, newer_notary_with_journal, garbage_with_journal, foreign_wal]


@pytest.mark.parametrize("make", [*MAKERS, stray_wal])
def test_a_refused_file_with_a_journal_or_wal_is_left_as_it_is(state, make):
    """SQLite recovers a hot journal or a WAL by writing; a refusal reads a copy instead."""
    path = state / "notary.sqlite"
    make(path)
    assert len(files(path)) == 2  # the file and its -journal or -wal
    before = files(path)
    with pytest.raises(IntakeRefused) as caught:
        refuse_unmigrated(path)
    assert caught.value.code == "NOTARY_SCHEMA_MISMATCH"
    for code, out in (route(state, "check", stdin="{}"), cli(state, "migrate-notary")):
        assert (code, out["error"]) == (2, "NOTARY_SCHEMA_MISMATCH"), out
    assert files(path) == before and not list(state.glob("*.bak"))


def test_a_closed_wal_file_is_refused_unopened(state):
    """WAL mode is in the header even with no -wal beside it; opening would make one."""
    path = state / "notary.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE other (x)")
    conn.close()
    before = files(path)
    assert list(before) == [path.name]
    for code, out in (route(state, "check", stdin="{}"), cli(state, "migrate-notary")):
        assert (code, out["error"]) == (2, "NOTARY_SCHEMA_MISMATCH"), out
    assert files(path) == before


@pytest.mark.skipif(ROOT, reason="root writes any file")
def test_a_read_only_notary_with_a_journal_is_read_but_not_migrated(state):
    path = state / "notary.sqlite"
    Notary(path)
    crash(path)
    path.chmod(0o444)
    before = files(path)
    refuse_unmigrated(path)  # current, read from a copy
    code, out = cli(state, "migrate-notary")
    assert (code, out["error"]) == (2, "BAD_STATE"), out
    assert files(path) == before


@pytest.mark.parametrize("bit", [stat.S_ISGID, stat.S_ISVTX])
def test_a_setgid_or_sticky_state_is_accepted(state, bit):
    """Only the permission bits must be 0700: a setgid (or sticky) directory still passes."""
    state.chmod(0o700 | bit)
    assert state.stat().st_mode & 0o7777 == 0o700 | bit  # the bit took
    Intake(state).check_state()
    Notary(state / "notary.sqlite")
    code, out = cli(state, "migrate-notary")
    assert (code, out["migrated"]) == (0, False), out


@pytest.mark.skipif(ROOT, reason="root reads any file")
def test_an_unreadable_notary_is_bad_state(state):
    path = state / "notary.sqlite"
    Notary(path)
    path.chmod(0o000)
    try:
        with pytest.raises(IntakeRefused) as caught:
            refuse_unmigrated(path)
        assert caught.value.code == "BAD_STATE"
        for code, out in (route(state, "check", stdin="{}"), cli(state, "migrate-notary")):
            assert (code, out["error"]) == (2, "BAD_STATE"), out
    finally:
        path.chmod(0o600)


def test_a_busy_notary_is_not_called_a_mismatch(state, monkeypatch):
    """Only "not a database" is a fact about the file; a lock held by a writer is not."""
    path = state / "notary.sqlite"
    Notary(path)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")

    def no_wait(path, mode):  # the probe's own query, without its 30-second busy wait
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=0)
        return conn.execute("PRAGMA user_version").fetchone()

    monkeypatch.setattr(notary_migrate, "_read_schema", no_wait)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            refuse_unmigrated(path)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_a_current_notary_is_never_written_by_check_or_migrate(state):
    path = state / "notary.sqlite"
    Notary(path)
    before = digest(path)
    refuse_unmigrated(path)
    route(state, "check", stdin="{}")
    code, out = cli(state, "migrate-notary")
    assert (code, out) == (0, {"migrated": False, "from": SCHEMA_VERSION, "to": SCHEMA_VERSION})
    assert digest(path) == before
