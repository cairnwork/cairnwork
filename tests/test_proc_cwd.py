"""#301: proc_cwd ties an owner process whose environment macOS withholds to an attempt by its
working directory. ``attempt_dirs`` and the KERN_PROCARGS2 parser are platform-neutral; the
process readers answer None off macOS. No environment or argument of a real process is read
here beyond a count."""

import os
import subprocess
import sys
import time

import pytest

from cairnwork import proc_cwd

ATTEMPT = "a0123456789abcdef"
macos = pytest.mark.skipif(sys.platform != "darwin", reason="the process readers are macOS-only")


def _dirs(root, *names):
    for name in names:
        (root / name).mkdir(parents=True)


def test_attempt_dirs_are_the_attempts_own_real_directories(tmp_path):
    root = tmp_path / "runs"
    own = [ATTEMPT, f"{ATTEMPT}-q-v0", f"{ATTEMPT}-q-v0.work", f"{ATTEMPT}-v-v12.priv"]
    _dirs(root, *own, f"{ATTEMPT}-e-v3")
    not_ours = [f"{ATTEMPT}-x-q-v0", f"{ATTEMPT}0-q-v0", f"{ATTEMPT}-q-v", f"{ATTEMPT}-q-v0x"]
    _dirs(root, *not_ours, f"{ATTEMPT}-z-v0", f"x{ATTEMPT}-q-v0", "other")
    (root / f"{ATTEMPT}-r-v1").write_text("a file, not a directory")
    found = proc_cwd.attempt_dirs([root], ATTEMPT)
    expected = {root.resolve() / n for n in [*own, f"{ATTEMPT}-e-v3"]}
    assert set(found) == expected and len(found) == len(expected)  # realpaths (tmp is a symlink)


def test_another_attempt_whose_id_extends_this_one_does_not_match(tmp_path):
    root = tmp_path / "runs"
    _dirs(root, "a1-x-q-v0", "a1-x", "a1x-q-v0")
    assert proc_cwd.attempt_dirs([root], "a1") == ()


@pytest.mark.parametrize(
    ("name", "target"),
    [
        (f"{ATTEMPT}-q-v0", "runs/inside"),  # a real directory inside the root
        (f"{ATTEMPT}-q-v1.work", "outside"),  # its realpath leaves the root
        (ATTEMPT, "outside"),
        (f"{ATTEMPT}-r-v0", "dangling"),
    ],
)
def test_attempt_dirs_never_follow_a_symlink(tmp_path, name, target):
    root = tmp_path / "runs"
    _dirs(root, "inside", f"{ATTEMPT}-p-v0")
    (tmp_path / "outside").mkdir()
    (root / name).symlink_to(tmp_path / target)
    assert proc_cwd.attempt_dirs([root], ATTEMPT) == ((root / f"{ATTEMPT}-p-v0").resolve(),)


def test_a_root_reached_through_a_symlink_gives_real_paths_inside_it(tmp_path):
    real = tmp_path / "real"
    _dirs(real, f"{ATTEMPT}-q-v0")
    (tmp_path / "link").symlink_to(real)
    assert proc_cwd.attempt_dirs([tmp_path / "link"], ATTEMPT) == (
        (real / f"{ATTEMPT}-q-v0").resolve(),
    )


@pytest.mark.parametrize("attempt", ["", "-q", ".x", "../runs", "a/b", "a" * 65, "a b", "_a"])
def test_a_malformed_attempt_id_gives_no_directories(tmp_path, attempt):
    root = tmp_path / "runs"
    _dirs(root, "-q-v0", "-q", ".x", "a" * 65, "a b", "_a", "_a-q-v0")
    assert proc_cwd.attempt_dirs([root], attempt) == ()


def test_an_entry_that_fails_its_check_drops_only_itself(tmp_path, monkeypatch):
    # Opus review of #301: one entry removed between the listing and its lstat must not drop
    # the other directories already found under the same root.
    root = tmp_path / "runs"
    _dirs(root, ATTEMPT, f"{ATTEMPT}-q-v0", f"{ATTEMPT}-v-v1")
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if os.path.basename(path) == f"{ATTEMPT}-q-v0":
            raise FileNotFoundError(path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(proc_cwd.os, "lstat", lstat)
    found = proc_cwd.attempt_dirs([root], ATTEMPT)
    assert set(found) == {root.resolve() / ATTEMPT, root.resolve() / f"{ATTEMPT}-v-v1"}


def test_attempt_dirs_skip_a_missing_file_or_unreadable_root(tmp_path):
    afile = tmp_path / "file"
    afile.write_text("x")
    locked, good = tmp_path / "locked", tmp_path / "good"
    _dirs(locked, f"{ATTEMPT}-q-v0")
    _dirs(good, f"{ATTEMPT}-q-v0")
    locked.chmod(0)
    try:
        found = proc_cwd.attempt_dirs([tmp_path / "missing", afile, locked, good], ATTEMPT)
    finally:
        locked.chmod(0o700)
    assert found == ((good / f"{ATTEMPT}-q-v0").resolve(),)


def _procargs(argv, env, *, path=b"/bin/x", pad=None, apple=()):
    """A KERN_PROCARGS2 buffer: argc, exec path NUL-padded to 8 bytes (or ``pad`` NULs), argv,
    env, an empty string, then the 'apple' strings. Values are dummies."""
    pad = (-len(path) % 8 or 8) if pad is None else pad
    strings = b"".join(s + b"\0" for s in [*argv, *env])
    tail = b"\0" + b"".join(s + b"\0" for s in apple)
    return len(argv).to_bytes(4, sys.byteorder) + path + b"\0" * pad + strings + tail


@pytest.mark.parametrize(
    ("raw", "count"),
    [
        (_procargs([b"/bin/sleep", b"60"], []), 0),
        (_procargs([b"/bin/sleep", b"60"], [], apple=[b"executable_path=/bin/x"]), 0),
        (_procargs([b"py", b""], [b"DUMMY=1", b"OTHER=2"]), 2),
        (_procargs([b"py"], [b"DUMMY=1"], apple=[b"ptr_munge="]), 1),
        (_procargs([b"py"], [b"DUMMY=1"], path=b"/bin/xy"), 1),  # path + NUL fills 8 bytes
        (_procargs([b"py"], [b"DUMMY=1"], path=b"/bin/xyz"), 1),  # a whole 8 more of padding
        (_procargs([b"", b"-c"], [b"DUMMY=1"]), 1),  # an empty argv[0] is not padding
        (_procargs([b"", b""], [b"DUMMY=1", b"OTHER=2"]), 2),
        (_procargs([b"py"], [b"DUMMY=1"], pad=1), None),  # padding short of the 8-byte mark
        (_procargs([b"py"], [b"DUMMY=1"]).replace(b"/bin/x\0\0", b"/bin/x\0Z"), None),  # not NUL
        (_procargs([b"py"], [])[:4] + b"/bin/x\0", None),  # the padding runs off the end
        (_procargs([b"py"], [b"DUMMY=1"])[:-1], 1),  # no terminating empty string
        (b"\x01\x00", None),  # shorter than argc
        (_procargs([b"py"], [])[:4] + b"/bin/x", None),  # the exec path never ends
        ((5).to_bytes(4, sys.byteorder) + b"/bin/x\0a\0b\0", None),  # argv runs off the end
        ((-1).to_bytes(4, sys.byteorder, signed=True) + b"/bin/x\0a\0", None),
    ],
)
def test_the_environment_count_parser(raw, count):
    assert proc_cwd._environment_count(raw) == count


def _dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603 - fixed argv
    process.wait()
    return process.pid


def test_the_readers_answer_none_for_a_dead_process():
    pid = _dead_pid()
    assert proc_cwd.environment_withheld(pid) is None
    assert proc_cwd.cwd_of(pid) is None


def test_off_macos_nothing_is_read_or_killed(monkeypatch):
    monkeypatch.setattr(proc_cwd, "_MACOS", False)
    assert proc_cwd.environment_withheld(os.getpid()) is None
    assert proc_cwd.cwd_of(os.getpid()) is None
    row = (os.getpid(), os.getpgid(0), os.getuid(), set())  # never signalled: off macOS
    assert proc_cwd.kill_withheld_in([row], ("/",), os.getuid()) == ([], [], 0)


@macos
def test_an_empty_argv0_does_not_hide_a_readable_environment():
    # Codex review of #301: skipping every NUL after the exec path ate an empty argv[0], so a
    # one-entry environment counted as none and the process looked withheld (killable).
    exe = os.path.realpath(sys.executable)  # not an Apple platform binary: env is readable
    child = subprocess.Popen(  # noqa: S603 - fixed argv
        ["", "-c", "import time; print('up', flush=True); time.sleep(30)"],
        executable=exe,
        env={"DUMMY": "1"},
        stdout=subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == b"up\n"  # read only once the child is running
        assert proc_cwd.environment_withheld(child.pid) is False
    finally:
        child.kill()
        child.wait()


@macos
def test_an_ambiguous_platform_binary_is_left_alone(tmp_path):
    # Codex r2 of #301: XNU hides a platform binary's environment by skipping the NULs after
    # the exec path, so with an empty argv[0] its first entry shows through. The buffer then
    # looks like the readable one-entry case above, which must never be killed, so this one is
    # not counted withheld and not killed either: a known gap (see environment_withheld).
    where = os.path.realpath(tmp_path)
    child = subprocess.Popen(  # noqa: S603 - fixed argv
        ["", "30"], executable="/bin/sleep", env={"A": "1", "B": "2"}, cwd=where
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:  # wait for the exec: only the exec path is looked at
            raw = proc_cwd._sysctl((1, 49, child.pid), proc_cwd._argmax() or 0)
            if raw and raw[4:].startswith(b"/bin/sleep\0"):
                break
            time.sleep(0.05)
        assert proc_cwd.environment_withheld(child.pid) is False
        row = (child.pid, child.pid, os.getuid(), set())
        assert proc_cwd.kill_withheld_in([row], (where,), os.getuid()) == ([], [], 0)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


@macos
def test_own_process_shows_its_environment_and_its_cwd():
    assert proc_cwd.environment_withheld(os.getpid()) is False
    cwd = proc_cwd.cwd_of(os.getpid())
    assert cwd is not None and os.path.realpath(cwd) == os.path.realpath(os.getcwd())


@macos
def test_the_cwd_of_a_child_is_read(tmp_path):
    where = tmp_path / "here"
    where.mkdir()
    child = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        cwd=where,
        stdin=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin"},
    )
    try:
        cwd = proc_cwd.cwd_of(child.pid)
        assert cwd is not None and os.path.realpath(cwd) == str(where.resolve())
    finally:
        child.kill()
        child.wait()


def test_attempt_dirs_returns_blackbox_vbb_directories(tmp_path):
    root = tmp_path / "runs"
    own = [f"{ATTEMPT}-v-vbb", f"{ATTEMPT}-v-vbb.work"]
    _dirs(root, *own)
    found = proc_cwd.attempt_dirs([root], ATTEMPT)
    expected = {root.resolve() / n for n in own}
    assert set(found) == expected and len(found) == len(expected)


def test_another_attempt_vbb_dir_is_not_counted(tmp_path):
    root = tmp_path / "runs"
    _dirs(root, "a1-v-vbb", "a10-v-vbb", "a1x-v-vbb")
    assert proc_cwd.attempt_dirs([root], "a1") == ((root / "a1-v-vbb").resolve(),)


def test_near_miss_vbb_suffixes_are_not_counted(tmp_path):
    root = tmp_path / "runs"
    near_miss = [
        f"{ATTEMPT}-v-vbbx",
        f"{ATTEMPT}-v-bb",
        f"{ATTEMPT}-v-vb",
        f"{ATTEMPT}-v-vbbb",
        f"{ATTEMPT}-v-vBB",
    ]
    _dirs(root, *near_miss)
    assert proc_cwd.attempt_dirs([root], ATTEMPT) == ()


@macos
def test_a_crashed_blackbox_runs_cwd_only_leftover_is_killed(tmp_path):
    # #314: a blackbox run clones into <run_id>-vbb; a /bin/sleep it left behind (environment
    # withheld by macOS) is tied to the attempt by its working directory and killed.
    root = tmp_path / "runs"
    _dirs(root, f"{ATTEMPT}-v-vbb")
    where = os.path.realpath(root / f"{ATTEMPT}-v-vbb")
    child = subprocess.Popen(["/bin/sleep", "30"], env={"A": "1"}, cwd=where)  # noqa: S603
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:  # wait for the exec
            raw = proc_cwd._sysctl((1, 49, child.pid), proc_cwd._argmax() or 0)
            if raw and raw[4:].startswith(b"/bin/sleep\0"):
                break
            time.sleep(0.05)
        withheld = proc_cwd.environment_withheld(child.pid)
        if withheld is not True:  # as withheld_here in test_recovery_withheld_env.py
            pytest.skip(f"/bin/sleep's environment is not withheld here ({withheld!r})")
        dirs = proc_cwd.attempt_dirs([root], ATTEMPT)
        row = (child.pid, child.pid, os.getuid(), set())
        assert proc_cwd.kill_withheld_in([row], dirs, os.getuid()) == ([child.pid], [], 0)
        assert child.wait(timeout=10) == -9
    finally:
        child.kill()
        child.wait()
