"""#1858 round 2: the dead-run sweeps (recovery, cleanup) start from the run directories' birth
time, the entry check runs where the sweep runs (macOS), the home comes from the account
database, one bad directory skips only itself, and a fake start time of another shape is
read as it always was. Signals and process starts are recorders; no real process is touched."""

import os
import struct
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest
from test_recovery_withheld_env import ATTEMPT, _dispatched, _recover

import cairnwork.confined as confined
import cairnwork.recovery as recovery
from cairnwork import cleanup_sweep, proc_cwd
from cairnwork.confined import Sweep

DARWIN = sys.platform == "darwin"
UID = 501
LINUX_VERIFIER = "/var/lib/cairnwork/runs/c1.work/c1"


@pytest.fixture
def signals(monkeypatch):
    sent = []
    monkeypatch.setattr(proc_cwd.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    return sent


def _fake(monkeypatch, started, cwd):
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: struct.pack("<QQ", started[pid], 0))
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: True)
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: cwd[pid])


def _rows(*pids):
    return [(pid, pid, UID, set()) for pid in pids]


def test_dirs_since_is_the_earliest_birth_and_fails_closed(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    assert proc_cwd.dirs_since([]) is None
    assert proc_cwd.dirs_since([str(tmp_path / "gone")]) is None
    if DARWIN:
        born = proc_cwd.dirs_since([str(first), str(second)])
        assert born == min(getattr(os.stat(p), "st_birthtime", -1) for p in (first, second))
        assert born is not None and born <= time.time()
    monkeypatch.setattr(os, "stat", lambda p: SimpleNamespace())  # no st_birthtime (Linux)
    assert proc_cwd.dirs_since([str(first)]) is None


def test_recovery_sweeps_with_a_start_condition(tmp_path, monkeypatch):
    state, run_root = tmp_path / "state", tmp_path / "runs"
    state.mkdir()
    (run_root / f"{ATTEMPT}-q-v0.work").mkdir(parents=True)
    seen = []

    def sweep(var, value, **kw):
        seen.append(kw)
        return Sweep(listed=True)

    monkeypatch.setattr(recovery, "sweep_marked", sweep)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    ledger, journal = _dispatched(state, run_root, ATTEMPT)
    _recover(state, run_root, ledger, journal)
    assert seen and all(kw["cwd_dirs"] for kw in seen)
    for kw in seen:
        if DARWIN:  # bounded by the run directory's birth: the sweep is on
            assert isinstance(kw["cwd_since"], float) and kw["cwd_since"] <= time.time()
        else:
            assert kw["cwd_since"] is None


def test_the_dead_run_cleanup_look_has_a_start_condition(tmp_path):
    run_root = tmp_path / "runs"
    (run_root / f"{ATTEMPT}-q-v0.work").mkdir(parents=True)
    intake = SimpleNamespace(run_root=run_root, verifier_account=lambda: None)
    seen: dict = {}

    def sweep(*args, **kw) -> Sweep:
        seen.update(kw)
        return Sweep(listed=True)

    look = cleanup_sweep.look_for(sweep, intake, ATTEMPT, "mark")
    look()
    assert seen["cwd_dirs"] and seen["cwd_since"] == proc_cwd.dirs_since(seen["cwd_dirs"])
    assert (seen["cwd_since"] is not None) == DARWIN


def test_a_process_older_than_the_run_dirs_is_not_signalled(tmp_path, monkeypatch, signals):
    run = os.path.realpath(tmp_path) + "/runs/a1"
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [os.path.realpath(tmp_path)])
    _fake(monkeypatch, {1: 99, 2: 100, 3: 101}, dict.fromkeys((1, 2, 3), run + "/w"))
    found = proc_cwd.kill_withheld_in(_rows(1, 2, 3), [run], UID, roots=[run[:-3]], since=100.0)
    assert found == ([3], [], 0) and signals == [(3, 9)]


def test_the_fake_start_stand_in_of_the_old_tests_still_reads(tmp_path, monkeypatch, signals):
    run = os.path.realpath(tmp_path) + "/runs/a1"
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [os.path.realpath(tmp_path)])
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: b"same start")  # 10 bytes
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: True)
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: run + "/w")
    assert proc_cwd.kill_withheld_in(_rows(7), [run], UID, roots=[run[:-3]], since=100.0) == (
        [],
        [],
        0,
    )
    assert signals == []  # a real bound cannot be checked against a value it cannot read
    assert proc_cwd.kill_withheld_in(_rows(7), [run], UID, roots=[run[:-3]], since=0.0) == (
        [7],
        [],
        0,
    )


def test_a_faked_macos_on_a_linux_temp_dir_keeps_its_temp_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_DARWIN", False)  # a Linux runner: temp is /tmp-shaped
    base = os.path.realpath(tmp_path)
    assert proc_cwd._temps() == [os.path.realpath(tempfile.gettempdir())]
    assert proc_cwd.scope([f"{base}/r/a1"], [f"{base}/r"]) == (f"{base}/r/a1",)


def test_the_linux_verifier_account_path_may_start_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr(proc_cwd, "_MACOS", False)  # nothing is swept here, so nothing to scope
    log = tmp_path / "run.log"
    with pytest.raises(FileNotFoundError):  # past the entry check: the directory is not here
        confined.execute(["true"], LINUX_VERIFIER, {confined.RUN_TOKEN_VAR: "t"}, 5, log)
    assert proc_cwd.entry_dirs(LINUX_VERIFIER, ()) == (os.path.realpath(LINUX_VERIFIER),)
    assert proc_cwd.entry_dirs("/state/owner/x", ()) == ("/state/owner/x",)


@pytest.mark.parametrize("bad", ["/", "/Users", "/tmp/a/b", "/Users/Shared/x", "/private/tmp"])
def test_on_macos_the_incident_shapes_are_still_refused_at_entry(monkeypatch, bad):
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_DARWIN", True)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: "/private/var/folders/ab/cd/T")
    monkeypatch.setattr(proc_cwd, "_home", lambda: "/Users/tester")
    with pytest.raises(ValueError):
        proc_cwd.entry_dirs(bad, ())


def test_home_comes_from_the_account_database_not_the_environment(monkeypatch):
    entry = SimpleNamespace(pw_dir="/Users/tester")
    monkeypatch.setattr(proc_cwd.pwd, "getpwuid", lambda uid: entry)
    monkeypatch.setenv("HOME", "/Users")  # a moved variable would make /Users/x/y/z "narrow"
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [])
    assert not proc_cwd._root_ok("/Users/someone/work/clones")
    assert proc_cwd._root_ok("/Users/tester/a/b/c")


def test_one_bad_dir_skips_only_itself(monkeypatch, tmp_path, signals):
    base = os.path.realpath(tmp_path)
    good, linked = f"{base}/r/a1", f"{base}/r/a2"
    os.makedirs(f"{base}/r")
    os.symlink("/", linked)
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [base])
    _fake(monkeypatch, {1: 10, 2: 10}, {1: good + "/w", 2: "/x"})
    found = proc_cwd.kill_withheld_in(
        _rows(1, 2), [linked, good], UID, roots=[f"{base}/r"], since=1.0
    )
    assert found == ([1], [], 0) and signals == [(1, 9)]


@pytest.mark.parametrize("since", [0.0, -1.0])
def test_dirs_since_birth_at_or_below_zero_fails_closed(tmp_path, monkeypatch, since):
    (tmp_path / "a").mkdir()
    monkeypatch.setattr(os, "stat", lambda p: SimpleNamespace(st_birthtime=since))
    assert proc_cwd.dirs_since([str(tmp_path / "a")]) is None


def test_an_undeclared_temp_subtree_is_refused(monkeypatch, tmp_path):
    base = os.path.realpath(tmp_path)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [base])
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([f"{base}/a1"])
    assert proc_cwd.scope([f"{base}/runs/a1"], [f"{base}/runs"]) == (f"{base}/runs/a1",)


def test_a_tmpdir_below_the_private_temp_dir_still_names_that_temp_dir(monkeypatch):
    """A sealed run sets TMPDIR to a directory inside T: T is the root."""
    t = "/private/var/folders/ab/cdef12/T"
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_DARWIN", True)
    monkeypatch.setattr(os.path, "realpath", lambda p: p)
    for set_to, want in ((f"{t}/cw1353-x", [t]), (t, [t]), (f"{t}x/y", []), ("/private/tmp/a", [])):
        monkeypatch.setattr(tempfile, "gettempdir", lambda set_to=set_to: set_to)
        assert proc_cwd._temps() == want
    monkeypatch.undo()
