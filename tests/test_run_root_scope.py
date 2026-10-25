"""#247: a test's cleanup of the shared run root removes only its own names, under the lock.

No verifier account is needed: a temporary directory stands in for the run root and a fake
account records the lock and the removals.
"""

import contextlib
import os
import shutil
from pathlib import Path

from run_root_scope import own_run_names


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
