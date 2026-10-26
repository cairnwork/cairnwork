"""#301: proc_cwd ties an owner process whose environment macOS withholds to an attempt by its
working directory. ``attempt_dirs`` and the KERN_PROCARGS2 parser are platform-neutral; the
process readers answer None off macOS. No environment or argument of a real process is read
here beyond a count."""

import os
import subprocess
import sys

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


def _procargs(argv, env, *, pad=3, apple=()):
    """A KERN_PROCARGS2 buffer: argc, exec path, NUL padding, argv, env, an empty string, then
    the 'apple' strings. Values are dummies."""
    strings = b"".join(s + b"\0" for s in [*argv, *env])
    tail = b"\0" + b"".join(s + b"\0" for s in apple)
    return len(argv).to_bytes(4, sys.byteorder) + b"/bin/x" + b"\0" * pad + strings + tail


@pytest.mark.parametrize(
    ("raw", "count"),
    [
        (_procargs([b"/bin/sleep", b"60"], []), 0),
        (_procargs([b"/bin/sleep", b"60"], [], apple=[b"executable_path=/bin/x"]), 0),
        (_procargs([b"py", b""], [b"DUMMY=1", b"OTHER=2"]), 2),
        (_procargs([b"py"], [b"DUMMY=1"], pad=1, apple=[b"ptr_munge="]), 1),
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
