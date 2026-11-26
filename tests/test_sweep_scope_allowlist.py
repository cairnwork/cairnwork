"""#1849: the cwd sweep is an allow-list. A directory is swept (or a run started in it) only when
it is strictly inside the user's private temp dir, or inside a run root the caller declares (itself
narrow). Every signal and every process start is replaced by a recorder here: no real process is
read or touched, and each test asserts the recorder saw nothing it should not."""

import os
import signal
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from cairnwork import confined, proc_cwd

TEMP = "/private/var/folders/ab/cdef12/T"
HOME = "/Users/tester"
GOOD_TEMP = f"{TEMP}/pytest-of-u/pytest-0/test_x"
RUNS = f"{HOME}/.local/state/cairnwork/runs"
UID = 501
CALLS: list[Any] = []


@pytest.fixture(autouse=True)
def recorder(monkeypatch, tmp_path):  # tmp_path first: it is made before gettempdir is faked
    CALLS.clear()

    def record(name):
        def call(*args, **kwargs):
            CALLS.append((name, args, kwargs))
            raise RuntimeError(f"{name} must not run in this test")

        return call

    monkeypatch.setattr(os, "kill", lambda pid, sig: CALLS.append(("kill", pid, sig)))
    monkeypatch.setattr(os, "killpg", record("killpg"))
    monkeypatch.setattr(subprocess, "run", record("run"))
    monkeypatch.setattr(subprocess, "Popen", record("Popen"))
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_DARWIN", True)  # the temp-dir shape is enforced as on macOS
    monkeypatch.setattr(tempfile, "gettempdir", lambda: TEMP)
    monkeypatch.setattr(proc_cwd, "_home", lambda: HOME)


def _no_signal():
    assert [c for c in CALLS if c[0] != "kill"] == [] and CALLS == []


BAD_PATHS = [
    "/", "//", "/.", "/private", "/tmp", "/private/tmp", "/tmp/a/b", "/private/tmp/a/b",
    "/var/folders", "/private/var/folders/ab", "/private/var/folders/zz/yy/T/x", "/var/tmp/a/b",
    "/Users", HOME, f"{HOME}/workspace", f"{HOME}/workspace/repo", f"{HOME}/Desktop/x",
    f"{HOME}/Library", f"{HOME}/Documents", f"{HOME}/.claude", "/Volumes/x/y/z",
    "/System/Volumes/Data/Users/tester/a/b/c", "/System/Volumes/Data", "/opt/homebrew/x",
    "/usr/local/x", "/Users/Shared/x", "/Applications/Foo.app/x", "/private/tmp/claude-501",
    "/private/tmp/x", "/PRIVATE/TMP/a/b", "/Users/TESTER/workspace", TEMP,
    "/private/var/folders/ab/cdef12",
    f"{TEMP}/../x", f"{TEMP}/x/../..", f"{RUNS}/a1/..",
]  # fmt: skip
MALFORMED = ["", ".", "..", "a/b", f"{TEMP}/x/./y", f"{GOOD_TEMP}/..", b"relative", b""]
TYPED = [b"/", b"/tmp/a/b", Path("/"), Path("/tmp/a/b"), Path(HOME), b"/Users/tester/workspace"]


@pytest.mark.parametrize("bad", [*BAD_PATHS, *MALFORMED, *TYPED])
def test_scope_refuses(bad):
    with pytest.raises(ValueError):
        proc_cwd.scope([bad])
    _no_signal()


@pytest.mark.parametrize("bad", [*BAD_PATHS, *MALFORMED, *TYPED])
def test_kill_withheld_in_with_no_rows_touches_nothing(bad):
    try:
        found = proc_cwd.kill_withheld_in([], [bad], UID, since=0.0)
    except ValueError:
        found = ([], [], 0)
    assert found == ([], [], 0)
    _no_signal()


@pytest.mark.parametrize("bad", MALFORMED)
def test_kill_withheld_in_malformed_raises(bad):
    with pytest.raises(ValueError):
        proc_cwd.kill_withheld_in([], [bad], UID, since=0.0)


@pytest.mark.parametrize("bad", [*BAD_PATHS, *MALFORMED, *TYPED])
def test_execute_refuses_before_anything_starts(bad, tmp_path):
    log = tmp_path / "run.log"
    env = {confined.RUN_TOKEN_VAR: "tok"}
    with pytest.raises(ValueError):
        confined.execute(["true"], bad, env, 1, log)
    assert not log.exists()
    _no_signal()


@pytest.mark.parametrize(
    "single", ["/", GOOD_TEMP + "/x", b"/", GOOD_TEMP.encode() + b"/x", RUNS + "/a1"]
)
def test_one_str_or_bytes_is_not_a_collection(single):
    with pytest.raises(ValueError):
        proc_cwd.scope(single, [RUNS])
    with pytest.raises(ValueError):
        proc_cwd.kill_withheld_in([], single, UID, roots=[RUNS], since=0.0)
    with pytest.raises(ValueError):
        proc_cwd.scope([GOOD_TEMP + "/x"], RUNS)  # one root string
    _no_signal()


def test_a_generator_is_read_once_and_the_checked_value_is_the_used_one():
    one = (d for d in [f"{GOOD_TEMP}/a1"])
    assert proc_cwd.scope(one) == (f"{GOOD_TEMP}/a1",)
    assert proc_cwd.scope(one) == ()  # spent: nothing left to be checked or used
    mixed = (d for d in [f"{GOOD_TEMP}/a1", "/"])
    with pytest.raises(ValueError):
        proc_cwd.scope(mixed)
    assert proc_cwd.kill_withheld_in([], (d for d in [f"{GOOD_TEMP}/a1"]), UID, since=0.0) == (
        [],
        [],
        0,
    )
    _no_signal()


@pytest.mark.parametrize("target", ["/", "/Users", HOME, "/private/tmp"])
def test_a_symlink_is_judged_by_its_target(tmp_path, monkeypatch, target):
    real = os.path.realpath(tmp_path)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [real])
    link = tmp_path / "a1"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        proc_cwd.scope([str(link)])
    with pytest.raises(ValueError):
        proc_cwd.scope([str(link / "x" / "y")])
    log = tmp_path / "log"
    with pytest.raises(ValueError):
        confined.execute(["true"], link, {confined.RUN_TOKEN_VAR: "t"}, 1, log)
    assert not log.exists()
    row = (4242, 4242, UID, set())
    assert proc_cwd.kill_withheld_in([row], [str(link)], UID, since=0.0) == ([], [], 0)
    _no_signal()


@pytest.mark.parametrize(
    "where, roots",
    [
        (f"{GOOD_TEMP}/a1", ()),
        (f"{TEMP}/x", ()),
        (f"{RUNS}/a0123456789abcdef", [RUNS]),
        (f"{RUNS}/a0123456789abcdef-q-v0", [RUNS]),
        ("/Users/Shared/cairnwork/runs/a0123456789abcdef", ["/Users/Shared/cairnwork/runs"]),
        (f"{HOME}/.cache/cairnwork/clones/c1/wd", [f"{HOME}/.cache/cairnwork/clones/c1"]),
    ],
)
def test_scope_allows(where, roots):
    assert proc_cwd.scope([where], roots) == (where,)
    assert proc_cwd.scope([where], roots, resolve=True) == (os.path.realpath(where),)


@pytest.mark.parametrize(
    "root",
    [
        HOME,
        f"{HOME}/workspace",
        f"{HOME}/workspace/repo",
        "/tmp",
        "/",
        TEMP,
        "/Users/Shared",
        "/private/tmp",
    ],
)
def test_a_root_must_itself_be_narrow(root):  # fmt: skip
    with pytest.raises(ValueError):
        proc_cwd.scope([f"{root}/a/b"], [root])
    with pytest.raises(ValueError):
        proc_cwd.scope([root], [root])
    assert proc_cwd.kill_withheld_in([], [f"{root}/a/b"], UID, roots=[root], since=0.0) == (
        [],
        [],
        0,
    )
    _no_signal()


def test_a_dir_equal_to_its_root_is_not_deep_enough():
    with pytest.raises(ValueError):
        proc_cwd.scope([RUNS], [RUNS])
    with pytest.raises(ValueError):
        proc_cwd.scope([RUNS.upper()], [RUNS])  # same directory on a case-insensitive disk


def test_the_cwd_of_this_test_is_not_allowed():
    here = os.getcwd()
    for where in (here, os.path.dirname(here)):
        with pytest.raises(ValueError):
            proc_cwd.scope([where])


@pytest.mark.parametrize(
    "odd", ["/private/tmp/claude-501/sess", "/opt/odd/T", "/private/var/folders/T"]
)
def test_an_unusual_temp_dir_allows_nothing_inside_it_or_above(monkeypatch, odd):  # fmt: skip
    monkeypatch.setattr(tempfile, "gettempdir", lambda: odd)
    for where in (odd, f"{odd}/x", f"{odd}/x/y", os.path.dirname(odd), "/private/tmp"):
        with pytest.raises(ValueError):
            proc_cwd.scope([where])
    _no_signal()


def test_off_macos_the_temp_dir_is_a_strict_ancestor_check(monkeypatch, tmp_path):
    base = os.path.realpath(tmp_path / "a" / "b")
    monkeypatch.setattr(proc_cwd, "_MACOS", False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: base)
    for where in (base, os.path.dirname(base)):
        with pytest.raises(ValueError):
            proc_cwd.scope([where])
    assert proc_cwd.scope([f"{base}/c"]) == (f"{base}/c",)


def _fake(monkeypatch, started, cwd, withheld=True):
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: struct.pack("<QQ", started[pid], 0))
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: withheld)
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: cwd[pid])


def _rows(*pids):
    return [(pid, pid, UID, set()) for pid in pids]


def test_only_a_process_started_after_the_mark_in_the_run_dir_is_killed(monkeypatch, tmp_path):
    run = os.path.realpath(tmp_path) + "/a1"
    started = {1: 100, 2: 200, 3: 300, 4: 301}
    cwd = {1: run + "/x", 2: run, 3: run + "/work", 4: HOME + "/Desktop"}
    _fake(monkeypatch, started, cwd)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [os.path.realpath(tmp_path)])
    found = proc_cwd.kill_withheld_in(_rows(1, 2, 3, 4), [run], UID, since=200.0)
    assert found == ([3], [], 0)  # 1 older, 2 equal to the mark, 4 elsewhere
    assert CALLS == [("kill", 3, signal.SIGKILL)]


def test_without_a_mark_nothing_is_swept(monkeypatch, tmp_path):
    run = os.path.realpath(tmp_path) + "/a1"
    _fake(monkeypatch, {1: 500}, {1: run})
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [os.path.realpath(tmp_path)])
    assert proc_cwd.kill_withheld_in(_rows(1), [run], UID) == ([], [], 0)
    assert CALLS == []


def test_a_row_whose_start_time_is_unreadable_is_left_alone(monkeypatch):
    _fake(monkeypatch, {1: 500}, {1: f"{GOOD_TEMP}/a1"})
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: None)
    assert proc_cwd.kill_withheld_in(_rows(1), [f"{GOOD_TEMP}/a1"], UID, since=0.0) == ([], [], 0)
    assert CALLS == []


def test_a_generator_of_one_deep_dir_still_kills(monkeypatch):
    run = f"{GOOD_TEMP}/a1"
    _fake(monkeypatch, {7: 10}, {7: run + "/w"})
    found = proc_cwd.kill_withheld_in(_rows(7), (d for d in [run]), UID, since=1.0)
    assert found == ([7], [], 0) and CALLS == [("kill", 7, signal.SIGKILL)]


def test_a_run_dir_swapped_for_a_link_later_sweeps_nothing_and_does_not_raise(
    monkeypatch, tmp_path
):
    real = os.path.realpath(tmp_path)
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [real])
    link = tmp_path / "a1"
    for target in ("/", HOME, real, "/private/tmp"):
        link.unlink(missing_ok=True)
        link.symlink_to(target)
        _fake(monkeypatch, {5: 10}, {5: target})
        assert proc_cwd.kill_withheld_in(_rows(5), [str(link)], UID, since=1.0) == ([], [], 0)
    assert CALLS == []


def test_only_the_rows_inside_are_killed_under_a_roots_attempt(monkeypatch, tmp_path):
    root = os.path.realpath(tmp_path)
    attempt = f"{root}/a1"
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [])
    monkeypatch.setattr(proc_cwd, "_root_ok", lambda r: r == root)
    _fake(monkeypatch, {1: 10, 2: 10}, {1: attempt + "/w", 2: f"{root}/a2/w"})
    found = proc_cwd.kill_withheld_in(_rows(1, 2), [attempt], UID, roots=[root], since=1.0)
    assert found == ([1], [], 0) and CALLS == [("kill", 1, signal.SIGKILL)]


def test_sweep_marked_passes_the_scope_down(monkeypatch):
    seen = {}

    def fake(rows, dirs, mine, seen_argv=None, roots=(), since=None):
        seen.update(dirs=dirs, roots=roots, since=since)
        return [], [], 0

    monkeypatch.setattr(proc_cwd, "kill_withheld_in", fake)
    row = (1, 1, os.getuid(), set())
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: [row])
    confined.sweep_marked("V", "x", cwd_dirs=(f"{GOOD_TEMP}/a1",), cwd_roots=[RUNS], cwd_since=3.0)
    assert seen == {"dirs": (f"{GOOD_TEMP}/a1",), "roots": [RUNS], "since": 3.0}
    assert CALLS == []
