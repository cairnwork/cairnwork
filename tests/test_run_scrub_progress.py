"""Issue #437: a candidate's tree must not hold the account cleanup at a limit for good.

A deep directory tree with an entry the account must unlock at the bottom, or more locked entries
than one walk may list, used to stop every walk at the same place: rm could not finish, and every
later account run was refused until root cleaned up. Each scrub, and each pass of the printed
account steps, must now get further than the last. Same setup as ``test_run_scrub_unlock``: the
real commands as the current user, no sudo, no verifier account, everything under ``tmp_path``.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import cairnwork.run_scrub as run_scrub
from cairnwork.run_scrub import close_private, stale_run_error
from tests.test_run_scrub_unlock import (
    HIDDEN,
    _acl,
    _private,
    _Refusing,
    _run_printed,
    _SameUser,
    _unlock_here,
)

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="macOS flags and ACLs")
SMALL = (16, 45)  # entries, seconds: a budget the trees below outgrow


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    yield root
    if sys.platform == "darwin":
        for name in os.listdir(tmp_path):
            _unlock_here(str(tmp_path / name))
    for name in os.listdir(tmp_path):
        subprocess.run(["/bin/rm", "-rf", "--", str(tmp_path / name)], check=False)
    assert os.listdir(tmp_path) == []


def _locked_file(path: Path) -> None:
    path.write_text("x")
    _acl(HIDDEN, str(path))
    os.chflags(path, stat.UF_IMMUTABLE)  # the syscall: the ACL above hides it from lstat


def _chain(top: Path, depth: int, sibling: bool = False) -> None:
    """``depth`` nested directories, made by relative names, a locked file at the bottom (and,
    with ``sibling``, one next to each directory, listed after it)."""
    back = os.open(".", os.O_RDONLY)
    try:
        os.chdir(top)
        for _ in range(depth):
            os.mkdir("d")
            if sibling:
                _locked_file(Path("z"))
            os.chdir("d")
        _locked_file(Path("f"))
    finally:
        os.fchdir(back)
        os.close(back)


def _shape(home: Path, kind: str) -> None:
    if kind == "chain":
        _chain(home, 60)
    elif kind == "chain-with-locked-siblings":
        _chain(home, 30, sibling=True)
    elif kind == "wide":
        for index in range(40):
            _locked_file(home / f"f{index}")
    else:  # every directory holds one locked file
        for index in range(30):
            (home / f"d{index}").mkdir()
            _locked_file(home / f"d{index}" / "f")


@darwin
def test_a_lock_deeper_than_the_old_depth_limit_is_cleared_in_one_pass(runs):
    """The real limits: 450 levels (the walk used to stop at 400) and a locked file below."""
    private = _private(runs)
    _chain(private / "run" / "home", 450)
    account = _SameUser(runs)
    close_private(private, account)
    assert not os.path.lexists(private / "run"), account.ran
    assert [rc for _argv, rc, _err in account.ran] == [0, 0], account.ran
    assert stale_run_error(_Refusing(runs)) is None


@darwin
@pytest.mark.parametrize("kind", ["chain", "chain-with-locked-siblings", "wide", "dirs"])
def test_past_the_entry_limit_each_scrub_gets_further(runs, monkeypatch, kind):
    """A tree larger than one walk's budget is refused at first, then gone after a few scrubs:
    a pass does not start over where the last one stopped."""
    monkeypatch.setattr(run_scrub, "_UNLOCK_LIMITS", SMALL)
    private = _private(runs)
    _shape(private / "run" / "home", kind)
    account = _SameUser(runs)
    close_private(private, account)
    assert os.path.lexists(private / "run")  # the budget really was too small
    refusals = 0
    while stale_run_error(account) is not None:
        refusals += 1
        assert refusals < 12, account.ran[-2:]
    assert not os.path.lexists(private / "run")
    assert stale_run_error(_Refusing(runs)) is None


@darwin
def test_the_printed_account_steps_repeated_clear_a_tree_past_a_limit(runs, monkeypatch):
    """A pass of the printed account steps at a limit is not bound to fail: repeated, they clear
    the tree, then the owner's steps close private, and root is never needed."""
    monkeypatch.setattr(run_scrub, "_UNLOCK_LIMITS", SMALL)
    private = _private(runs)
    _chain(private / "run" / "home", 60, sibling=True)
    message = stale_run_error(_Refusing(runs))
    assert message is not None
    steps = message[message.index("as the verifier account: ") :]
    account_steps = steps[: steps.index("; as root")]
    ran: list[tuple[list[str], int, str]] = []
    for _ in range(12):
        ran = _run_printed(account_steps)
        if not os.path.lexists(private / "run"):
            break
    assert not os.path.lexists(private / "run"), ran
    owner_steps = steps[steps.index("; as the owner: ") + 2 :]
    assert [rc for _argv, rc, _err in _run_printed(owner_steps)] == [0, 0]
    assert stale_run_error(_Refusing(runs)) is None
    assert "repeated" in message  # the root step says when it is still needed


def _walk(run: Path, limits: tuple[int, int], preamble: str) -> subprocess.CompletedProcess:
    """The walk with ``preamble`` run first in the same interpreter (a counter or a clock)."""
    argv = [sys.executable, "-I", "-c", f"{preamble}\n{run_scrub._UNLOCK}"]
    argv += [os.path.realpath(run), *map(str, limits)]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


# Counts the names every scandir yields; printed at exit, after the walk's own exit too.
_COUNTED = """\
import atexit, os, sys
real, read = os.scandir, [0]
class Counted:
    def __init__(self, fd):
        self.entries = real(fd)
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.entries.close()
    def __iter__(self):
        return self
    def __next__(self):
        entry = next(self.entries)
        read[0] += 1
        return entry
os.scandir = Counted
atexit.register(lambda: print("read", read[0]))
"""


def _names_read(done: subprocess.CompletedProcess) -> int:
    return int(done.stdout.split()[-1])


@darwin
def test_a_listing_reads_only_a_bounded_number_of_names(runs):
    """1,000 names with a budget of 10: at most 11 are read, not the whole directory."""
    top = runs / "top"
    top.mkdir()
    for index in range(1000):
        (top / f"f{index}").write_text("x")
    done = _walk(top, (10, 45), _COUNTED)
    assert done.returncode == 1 and "limit reached" in done.stderr, done
    assert 0 < _names_read(done) <= 11, done


@darwin
def test_with_no_time_left_no_name_is_read(runs):
    """With 0 seconds the clock stops the walk before its first listing."""
    top = runs / "top"
    top.mkdir()
    for index in range(5):
        (top / f"f{index}").write_text("x")
    done = _walk(top, (500_000, 0), _COUNTED)
    assert done.returncode == 1 and "limit reached" in done.stderr, done
    assert _names_read(done) == 0, done


@darwin
@pytest.mark.parametrize(("budget", "code"), [(17, 0), (16, 1)])
def test_the_entry_budget_boundary(runs, budget, code):
    """A listing keeps at most half the budget left, rounded up: 9 names fit 17, not 16."""
    top = runs / "top"
    top.mkdir()
    for index in range(9):
        (top / f"f{index}").write_text("x")
    done = _walk(top, (budget, 45), "")
    assert done.returncode == code, done
    assert sorted(os.listdir(top)) == sorted(f"f{index}" for index in range(9))


# A clock that moves one second per reading, so the deadline falls mid-walk every time.
_TICKING = "import itertools, time\ntime.monotonic = itertools.count().__next__"


@darwin
def test_at_the_clock_the_next_pass_goes_on_from_where_this_one_stopped(runs):
    """Stopped by the clock deep in a chain, the walk moves the directory it is in up into run,
    so rm removes the unlocked path above it and the next pass starts below it."""
    private = _private(runs)
    run = private / "run"
    _chain(run / "home", 60)
    for _ in range(12):
        done = _walk(run, (500_000, 30), _TICKING)
        subprocess.run(["/bin/rm", "-rf", "--", str(run)], capture_output=True, check=False)
        if not os.path.lexists(run):
            break
        assert done.returncode == 1 and "limit reached" in done.stderr, done
    assert not os.path.lexists(run)
