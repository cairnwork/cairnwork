"""Issue #343: a notary is migrated only by the explicit migrate-notary, after a backup."""

import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_notary import notarize
from verify_helpers import FIX, candidate, cli, make_mirror, verify

import cairnwork.notary_migrate as notary_migrate
import cairnwork.verify_cli as verify_cli
from cairnwork import runlock
from cairnwork.notary import Notary, NotarySchemaMismatch, NotarySchemaOlder
from cairnwork.notary_migrate import NotaryMissing, migrate_notary, refuse_unmigrated
from cairnwork.notary_schema import SCHEMA, SCHEMA_VERSION
from cairnwork.runlock import RunLockBusy

_ADDED_LATER = {1: ("expected_identity", "binding_json"), 2: ("binding_json",)}


def old_notary(path, version, rows_from=None):
    """A notary at an older schema (its real DDL minus later columns), with the rows of
    rows_from copied in when given."""
    dropped = _ADDED_LATER[version]
    ddl = "\n".join(line for line in SCHEMA.splitlines() if not line.strip().startswith(dropped))
    conn = sqlite3.connect(path)
    conn.executescript(f"{ddl}PRAGMA user_version = {version};")
    if rows_from is not None:
        columns = ", ".join(r[1] for r in conn.execute("PRAGMA table_info(core_verifications)"))
        src = sqlite3.connect(rows_from)
        rows = src.execute(f"SELECT {columns} FROM core_verifications").fetchall()
        src.close()
        marks = ",".join("?" * len(columns.split(", ")))
        conn.executemany(f"INSERT INTO core_verifications ({columns}) VALUES ({marks})", rows)
    conn.commit()
    conn.close()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version_of(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def rows_of(path, columns="*"):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT {columns} FROM core_verifications ORDER BY seq").fetchall()
    finally:
        conn.close()


def backups(state):
    return sorted(state.glob("notary.sqlite.*.bak"))


@pytest.fixture
def verified(repo, tmp_path):
    """A schema-3 notary holding one verified row, and that row's verdict."""
    path = tmp_path / "src.sqlite"
    return path, notarize(Notary(path), repo, {"src/a.py": "x = 2\n"})


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_notary_is_refused_on_open_and_not_written(verified, tmp_path, version):
    path = tmp_path / "old.sqlite"
    old_notary(path, version, rows_from=verified[0])
    before = digest(path)
    with pytest.raises(NotarySchemaOlder) as caught:
        Notary(path)
    assert caught.value.code == "NOTARY_SCHEMA_OLDER"
    assert isinstance(caught.value, NotarySchemaMismatch)  # existing except clauses still apply
    assert f"cairnwork-verify --state {tmp_path} migrate-notary" in str(caught.value)
    assert digest(path) == before and version_of(path) == version


def test_a_newer_notary_is_a_mismatch_and_migrate_leaves_it_alone(tmp_path):
    path = tmp_path / "notary.sqlite"
    Notary(path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    before = digest(path)
    with pytest.raises(NotarySchemaMismatch) as caught:
        Notary(path)
    assert caught.value.code == "NOTARY_SCHEMA_MISMATCH"
    with pytest.raises(NotarySchemaMismatch):
        migrate_notary(path)
    assert digest(path) == before and backups(tmp_path) == []


@pytest.mark.parametrize("version", [1, 2])
def test_migrate_state_backs_up_then_migrates(verified, state, version):
    src, verdict = verified
    path = state / "notary.sqlite"
    old_notary(path, version, rows_from=src)
    old_rows = rows_of(path)
    code, out = cli(state, "migrate-notary")
    assert code == 0, out
    [backup] = backups(state)
    assert out == {"migrated": True, "from": version, "to": SCHEMA_VERSION, "backup": str(backup)}
    assert version_of(backup) == version and rows_of(backup) == old_rows
    assert backup.stat().st_mode & 0o777 == 0o600  # owner-only, as the notary's state is
    assert version_of(path) == SCHEMA_VERSION
    width = len(old_rows[0])  # old columns first, unchanged; every added column is NULL
    assert [row[:width] for row in rows_of(path)] == old_rows
    assert all(value is None for row in rows_of(path) for value in row[width:])
    got = Notary(path).verdict("task-1", "a1", expected_candidate=verdict.candidate_commit)
    assert got is not None and got.verified


def test_migrate_state_on_a_current_notary_writes_nothing(state):
    Notary(state / "notary.sqlite")
    before = digest(state / "notary.sqlite")
    code, out = cli(state, "migrate-notary")
    assert (code, out) == (0, {"migrated": False, "from": SCHEMA_VERSION, "to": SCHEMA_VERSION})
    assert digest(state / "notary.sqlite") == before and backups(state) == []


def test_migrate_state_without_a_notary_creates_none(state):
    code, out = cli(state, "migrate-notary")
    assert code == 2 and out["error"] == NotaryMissing.code
    assert not (state / "notary.sqlite").exists() and backups(state) == []


def test_a_backup_name_that_exists_is_refused_and_nothing_migrates(tmp_path, monkeypatch):
    path = tmp_path / "notary.sqlite"
    old_notary(path, 2)
    before = digest(path)

    fixed = notary_migrate.datetime(2026, 10, 28, tzinfo=notary_migrate.UTC)

    class Fixed:
        @staticmethod
        def now(_tz):
            return fixed

    monkeypatch.setattr(notary_migrate, "datetime", Fixed)
    taken = tmp_path / "notary.sqlite.v2-20261028T000000000000Z.bak"
    taken.write_text("someone else's file")
    with pytest.raises(FileExistsError):
        migrate_notary(path)
    assert taken.read_text() == "someone else's file"
    assert not list(tmp_path.glob("*.tmp"))  # the copy made before the refusal is removed
    assert digest(path) == before and version_of(path) == 2


def test_a_failed_migration_rolls_back_and_keeps_the_backup(tmp_path, monkeypatch):
    path = tmp_path / "notary.sqlite"
    old_notary(path, 1)
    monkeypatch.setitem(notary_migrate.MIGRATIONS, 2, "ALTER TABLE missing ADD COLUMN x TEXT")
    with pytest.raises(sqlite3.OperationalError):
        migrate_notary(path)
    assert version_of(path) == 1  # the first ALTER rolled back with the failing second one
    conn = sqlite3.connect(path)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(core_verifications)")}
    conn.close()
    assert "expected_identity" not in columns
    assert [version_of(b) for b in backups(tmp_path)] == [1]


def _deferred_run(project, state, tmp_path):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    mirror = make_mirror(project, tmp_path)
    (state / "config.json").write_text(
        json.dumps({"verifier_account": False, "mirror": str(mirror)})
    )
    cand = candidate(project, base, {"src/calc.py": FIX})
    verifier = " ".join(CHECK[0][1])
    return lambda: verify(
        state, project, base, cand, "--test-commit", test_commit, tests=(verifier,)
    ), test_commit


def test_a_run_on_an_older_notary_is_refused_before_its_tests_are_used(project, state, tmp_path):
    old_notary(state / "notary.sqlite", 2)
    before = digest(state / "notary.sqlite")
    run, test_commit = _deferred_run(project, state, tmp_path)
    code, out = run()
    assert code == 2 and out["error"] == "NOTARY_SCHEMA_OLDER", out
    assert digest(state / "notary.sqlite") == before
    assert not (state / "verify-used-tests" / test_commit).exists()


@pytest.mark.sandbox
def test_after_migrate_state_the_same_run_verifies(project, state, tmp_path):
    old_notary(state / "notary.sqlite", 2)
    run, test_commit = _deferred_run(project, state, tmp_path)
    assert cli(state, "migrate-notary")[0] == 0
    code, out = run()
    assert code == 0, out
    assert (state / "verify-used-tests" / test_commit).exists()


def test_migrate_notary_without_a_state_dir_is_notary_missing(tmp_path):
    state = tmp_path / "absent"
    code, out = cli(state, "migrate-notary")  # documented as NOTARY_MISSING, exit 2 (#356)
    assert code == 2 and out["error"] == NotaryMissing.code, out
    assert not state.exists()


def test_the_refusal_leaves_an_empty_notary_file_unwritten(tmp_path):
    path = tmp_path / "notary.sqlite"
    path.touch()
    refuse_unmigrated(path)  # an empty file is no notary yet: the run creates it (#356)
    assert path.read_bytes() == b""


def test_route_reports_an_older_notary_as_refused(state):
    old_notary(state / "notary.sqlite", 2)
    done = subprocess.run(
        [sys.executable, "-I", "-m", "cairnwork.route", "--state", str(state), "recover"],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert done.returncode == 2 and json.loads(done.stdout)["error"] == "NOTARY_SCHEMA_OLDER"


def test_migrate_notary_refuses_a_state_others_can_use(state):
    old_notary(state / "notary.sqlite", 2)
    before = digest(state / "notary.sqlite")
    state.chmod(0o755)
    code, out = cli(state, "migrate-notary")
    assert code == 2 and out["error"] == "BAD_STATE", out
    assert digest(state / "notary.sqlite") == before and backups(state) == []


def test_migrate_notary_refuses_while_a_run_holds_the_run_lock(state):
    old_notary(state / "notary.sqlite", 2)
    before = digest(state / "notary.sqlite")
    with runlock.held(state / "ownership.sqlite", exclusive=False):  # as a running verify does
        code, out = cli(state, "migrate-notary")
    assert code == 2 and out["error"] == RunLockBusy.code, out
    assert digest(state / "notary.sqlite") == before and backups(state) == []


def test_the_run_lock_is_held_exclusively_while_migrating(state, monkeypatch, capsys):
    old_notary(state / "notary.sqlite", 2)
    real, attempts = verify_cli.migrate_notary, []

    def migrate_while_probing(path):
        with pytest.raises(RunLockBusy):  # a run (shared) or recovery (exclusive) cannot start
            with runlock.held(state / "ownership.sqlite", exclusive=True):
                pass
        attempts.append(path)
        return real(path)

    monkeypatch.setattr(verify_cli, "migrate_notary", migrate_while_probing)
    assert verify_cli.main(["--state", str(state), "migrate-notary"]) == 0
    assert json.loads(capsys.readouterr().out)["migrated"] is True and len(attempts) == 1
