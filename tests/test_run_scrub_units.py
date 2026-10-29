"""Issue #437: every account cleanup pass does at least one unit of work, whatever the clock.

A unit is an entry unlocked so that rm can remove it, or a directory at depth 2 or more moved up
into run/. After the clock the walk stops at the first point where it has done one, so a pass
can still not end with nothing done when one read of a listing outlasts all the time left.
Same setup as ``test_run_scrub_unlock``: the current user, no sudo, everything under
``tmp_path``.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import tests.test_run_scrub_progress as progress
from tests.test_run_scrub_progress import (
    _left_after_each_pass,
    _locked_file,
    _pass,
    _shrinks_to_nothing,
    _walk,
    _weight,
)
from tests.test_run_scrub_unlock import _private

runs = progress.runs  # the same fixture: flags and ACLs undone at teardown

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="macOS flags and ACLs")


# One listing call that returns only after all the time is gone, in one directory (by inode, so
# it stays slow after being moved up).
_ONE_SLOW_CALL = """\
import os, time
now, real = [0], os.scandir
time.monotonic = lambda: now[0]
class Jumping:
    def __init__(self, fd):
        self.entries, self.slow = real(fd), os.fstat(fd).st_ino == {ino}
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.entries.close()
    def __iter__(self):
        return self
    def __next__(self):
        if self.slow:
            now[0] += 10**6
        return next(self.entries)
os.scandir = Jumping
"""


@darwin
@pytest.mark.parametrize("where", ["right under run", "deeper"])
def test_one_listing_call_past_all_the_time_left_still_lets_each_pass_get_further(runs, where):
    """Each pass does at least one unit of work whatever the clock says: here a single read of
    a listing outlasts the whole pass, yet two passes in a row each remove an entry or move a
    directory up, and the tree is gone in the end."""
    private = _private(runs)
    run = private / "run"
    slow = run / "home" if where == "right under run" else run / "home" / "x"
    slow.mkdir(exist_ok=True)
    for index in range(6):
        _locked_file(slow / f"f{index}")
    preamble = _ONE_SLOW_CALL.format(ino=os.lstat(slow).st_ino)
    left = [_weight(run), _pass(run, (500_000, 30), preamble), _pass(run, (500_000, 30), preamble)]
    assert left[0] > left[1] > left[2], left
    left = _left_after_each_pass(run, (500_000, 30), preamble)
    assert _shrinks_to_nothing(left), left


# The lstat of one name shows another device, as a mount point's does.
_MOUNTED = """\
import os
real = os.lstat
class Mounted:
    st_dev = -1
    def __init__(self, info):
        self.info = info
    def __getattr__(self, attr):
        return getattr(self.info, attr)
def lstat(name, *args, **kwargs):
    info = real(name, *args, **kwargs)
    return Mounted(info) if name == "m" else info
os.lstat = lstat
"""


@darwin
def test_a_directory_on_another_device_is_left_and_the_walk_exits_1(runs):
    """Skipped, not entered, and reported: exit 1."""
    top = runs / "top"
    (top / "m").mkdir(parents=True)
    done = _walk(top, (500_000, 45), _MOUNTED)
    assert done.returncode == 1 and "limit reached" in done.stderr, done


@darwin
def test_a_cut_listing_counts_only_the_names_it_keeps(runs):
    """Budget 9: the first listing keeps 5 of 8 directories, so 4 names are left for theirs;
    4 of the 5 get their locked file unlocked and are removed, 4 directories stay. Counting
    the extra name read to see the cut would leave only 3 for them."""
    top = runs / "top"
    for index in range(8):
        (top / f"d{index}").mkdir(parents=True)
        _locked_file(top / f"d{index}" / "f")
    _walk(top, (9, 45), "")
    subprocess.run(["/bin/rm", "-rf", "--", str(top)], capture_output=True, check=False)
    assert len(os.listdir(top)) == 4


@darwin
def test_past_the_clock_deep_down_it_stops_at_depth_2_and_moves_that_up(runs):
    """With 0 seconds in a deep chain with no file until the bottom, the pass does not walk the
    whole chain to find a file to unlock: it stops on entering depth 2 and moves that up."""
    private = _private(runs)
    run = private / "run"
    back = os.open(".", os.O_RDONLY)
    try:
        os.chdir(run / "home")
        for _ in range(10):
            os.mkdir("d")
            os.chdir("d")
        _locked_file(Path("f"))
    finally:
        os.fchdir(back)
        os.close(back)
    done = _walk(run, (500_000, 0), progress._COUNTED)
    assert done.returncode == 1 and "limit reached" in done.stderr, done
    assert progress._names_read(done) == 3, done  # run, home and the first d each list one name
    moved = [name for name in os.listdir(run) if name.startswith(".unlock-")]
    assert len(moved) == 1 and os.listdir(run / moved[0]) == ["d"], os.listdir(run)


# The clock runs out right after one directory (by inode) is listed in full: its names are all
# there, and the pass may do only one of them.
_OUT_AFTER_LISTING = """\
import os, time
now, real = [0], os.fchdir
time.monotonic = lambda: now[0]
def fchdir(fd):
    real(fd)
    if os.fstat(fd).st_ino == {ino}:
        now[0] = 10**6
os.fchdir = fchdir
"""


@darwin
@pytest.mark.parametrize("kind", ["files", "empty directories"])
def test_past_the_clock_right_under_run_one_unit_is_done_then_it_stops(runs, kind):
    """Five locked files, or five locked empty directories, right under run's home: the clock
    runs out once they are listed; one is unlocked (a file) or finished (a directory), and the
    other four stay locked."""
    private = _private(runs)
    home = private / "run" / "home"
    for index in range(5):
        if kind == "files":
            _locked_file(home / f"f{index}")
        else:
            (home / f"e{index}").mkdir()
            os.chflags(home / f"e{index}", stat.UF_IMMUTABLE)  # rm cannot remove it until cleared
    done = _walk(
        private / "run", (500_000, 30), _OUT_AFTER_LISTING.format(ino=os.lstat(home).st_ino)
    )
    assert done.returncode == 1 and "limit reached" in done.stderr, done
    subprocess.run(["/bin/rm", "-rf", "--", str(private / "run")], capture_output=True, check=False)
    assert len(os.listdir(home)) == 4
