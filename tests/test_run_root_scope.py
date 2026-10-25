"""#247: a test's cleanup of the shared run root removes only its own names, under the lock.

No verifier account is needed: a temporary directory stands in for the run root and a fake
account records the lock and the removals.
"""

import contextlib
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from run_root_scope import own_attempt, own_run_names

from cairnwork.journal import Journal


class _LockedAccount:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.held = False
        self.removed_unlocked: list[Path] = []
        self.removed: list[Path] = []

    @contextlib.contextmanager
    def exclusive(self):
        assert not self.held, "the lock is not reentrant"
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def remove(self, path: Path) -> None:
        (self.removed if self.held else self.removed_unlocked).append(path)
        shutil.rmtree(path, ignore_errors=True)


def test_cleanup_removes_only_the_tests_own_names_and_holds_the_lock(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "old-v-v0").mkdir()  # there before the test
    account = _LockedAccount(runs)
    with own_run_names(runs, account) as own:
        own.append("mine1-")
        (runs / "mine1-v-v0.work").mkdir()
        (runs / "mine1-v-v0.work" / "f").write_text("x")
        (runs / "other-session-v-v0.work").mkdir()  # a live run of another session
        (runs / "other-session-v-v0.work" / "f").write_text("live")
    assert sorted(os.listdir(runs)) == ["old-v-v0", "other-session-v-v0.work"]
    assert (runs / "other-session-v-v0.work" / "f").read_text() == "live"
    assert account.removed == [runs / "mine1-v-v0.work"]
    assert account.removed_unlocked == []


def test_cleanup_takes_no_lock_when_the_test_made_nothing(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "other-v-v0").mkdir()
    account = _LockedAccount(runs)
    account.exclusive = lambda: (_ for _ in ()).throw(AssertionError("no lock needed"))  # type: ignore[method-assign]
    with own_run_names(runs, account):
        (runs / "foreign-v-v0").mkdir()
    assert (runs / "foreign-v-v0").exists() and (runs / "other-v-v0").exists()


def test_cleanup_reads_a_module_list_the_test_appends_to_later(tmp_path):
    """The verifier-account tests register names through a module-level list (own_run_id): the
    cleanup must read that same list at teardown, with what the test appended, not a copy."""
    runs = tmp_path / "runs"
    runs.mkdir()
    account = _LockedAccount(runs)
    module_list = ["stale-from-an-earlier-test-"]
    (runs / "stale-from-an-earlier-test-v0").mkdir()
    with own_run_names(runs, account, module_list):
        module_list.append("late1-")  # what own_run_id does inside the test body
        (runs / "late1-v-v0.work").mkdir()
    assert account.removed == [runs / "late1-v-v0.work"]
    assert (runs / "stale-from-an-earlier-test-v0").exists()


def test_own_attempt_reads_the_journal_keyed_by_the_generated_task_id(tmp_path):
    """run_task keys journal rows by its generated task id (task-<hex>), not the idempotency key
    the driver passes (k1): the wait in _kill_in_p2p must still find the attempt while it runs."""
    assert own_attempt(tmp_path) is None
    journal = Journal(tmp_path / "journal.sqlite")
    assert own_attempt(tmp_path) is None
    journal.record("task-90abfe7f", "a405d5d5a4f147e97", "DISPATCHED", "x")
    journal.record("task-90abfe7f", "a405d5d5a4f147e97", "WORKER_STARTED")
    assert own_attempt(tmp_path) == "a405d5d5a4f147e97"


def test_own_attempt_reports_an_unreadable_journal_instead_of_waiting(tmp_path):
    (tmp_path / "journal.sqlite").write_bytes(b"not a database, " * 64)
    with pytest.raises(sqlite3.DatabaseError):
        own_attempt(tmp_path)
