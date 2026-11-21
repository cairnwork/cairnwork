"""Issue #437 cleanup tests run account commands as this user, without sudo."""

import ctypes
import os
import shlex
import stat
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from run_scrub_unlock_harness import walk_source as _walk_source

import cairnwork.run_scrub as run_scrub
from cairnwork.run_scrub import account_delete_argv, close_private, stale_run_error
from cairnwork.verifier_account import VerifierAccountError

USER = "cairnwork-verifier"
SUDO = ["/usr/bin/sudo", "-n", "-u", USER, "--"]
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="macOS flags and ACLs")
HIDDEN = "everyone deny readsecurity,delete"  # then even the owner's lstat fails


def _ok(argv: list[str]) -> None:
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert done.returncode == 0, (argv, done.stderr)


def _acl(entry: str, name: str) -> None:
    """``chmod -h +a``: on ``name`` itself, a symlink too, as a candidate could."""
    _ok(["/bin/chmod", "-h", "+a", entry, name])


def _drop_acl(name: str) -> None:
    """Teardown's own ACL clear: setattrlist, FSOPT_NOFOLLOW, an empty filesec. No open."""
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.setattrlist.argtypes = (ctypes.c_char_p,) * 3 + (ctypes.c_size_t, ctypes.c_uint32)
    attrs = struct.pack("=HHIIIII", 5, 0, 0x400000, 0, 0, 0, 0)
    filesec = struct.pack("=I16s16sII", 0x012CC16D, bytes(16), bytes(16), 0xFFFFFFFF, 0)
    value = struct.pack("=iI", 8, len(filesec)) + filesec
    lib.setattrlist(os.fsencode(name), attrs, value, len(value), 1)


def _unlock_here(name: str) -> None:
    """Test teardown clears nested locks by relative names, without following links."""
    assert sys.platform == "darwin"  # lchflags and chflags exist only there
    try:
        os.lchflags(name, 0)
    except OSError:
        pass
    _drop_acl(name)
    try:
        info = os.lstat(name)
    except OSError:
        return
    if not stat.S_ISDIR(info.st_mode):
        return
    os.chmod(name, 0o700)
    back = os.open(".", os.O_RDONLY)
    try:
        os.chdir(name)
        for child in os.listdir("."):
            _unlock_here(child)
    finally:
        os.fchdir(back)
        os.close(back)


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
    assert os.listdir(tmp_path) == []  # nothing locked is left behind


class _Refusing:
    def __init__(self, root):
        self.user = USER
        self.run_root = root
        self.python = sys.executable  # stands in for the verifier python

    def remove(self, path) -> None:
        raise VerifierAccountError("no sudo in this test")


class _SameUser(_Refusing):
    """Runs the real ``account_delete_argv`` commands as the current user."""

    def __init__(self, root):
        super().__init__(root)
        self.ran: list[tuple[list[str], int, str]] = []

    def remove(self, path):
        real = os.path.realpath(path)
        for argv in account_delete_argv(USER, real, lambda inner: inner, self.python):
            assert argv[:5] == SUDO
            done = subprocess.run(argv[5:], capture_output=True, text=True, check=False)
            self.ran.append((argv[5:], done.returncode, done.stderr.strip()))


def _private(root: Path) -> Path:
    private = root / "rid-v0.abcd1234"
    (private / "run" / "home").mkdir(parents=True)
    (private / "verifier.log").write_text("kept")
    os.chmod(private, 0o750)
    return private


def _lock_down(home: Path) -> None:
    """Nest ACLs, flags and modes to defeat fixed recursive cleanup orders."""
    assert sys.platform == "darwin"
    deep = home / "a" / "b" / "c" / "-R"
    deep.mkdir(parents=True)
    for name in ("immutable", "append", "acl", "hidden-immutable"):
        (deep / name).write_text("x")
    (home / "acl-file").write_text("x")
    (home / "link").symlink_to(home / "acl-file")
    (home / "holder").mkdir()
    (home / "holder" / "f").write_text("x")
    _acl("everyone deny delete", str(home / "acl-file"))
    _acl(HIDDEN, str(home / "link"))
    _acl("everyone deny delete_child", str(home / "holder"))
    _acl("everyone deny delete", str(deep / "acl"))
    _acl(HIDDEN, str(deep / "hidden-immutable"))
    os.chflags(deep / "hidden-immutable", stat.UF_IMMUTABLE)  # the syscall: no stat needed
    _ok(["/usr/bin/chflags", "uchg", str(deep / "immutable")])
    _ok(["/usr/bin/chflags", "uappnd", str(deep / "append")])
    _acl("everyone deny list,search", str(deep))
    os.chmod(deep.parent, 0o100)
    _ok(["/usr/bin/chflags", "uappnd", str(deep.parent)])
    os.chmod(deep.parent.parent, 0o000)
    _ok(["/usr/bin/chflags", "uchg", str(deep.parent.parent)])
    _acl("everyone deny list,search,readsecurity", str(home / "a"))


def test_every_account_command_succeeds_on_a_plain_tree(runs):
    """BSD getopt needs ``--`` before paths that resemble options."""
    private = _private(runs)
    (private / "run" / "home" / "d").mkdir()
    (private / "run" / "home" / "d" / "f").write_text("x")
    account = _SameUser(runs)
    close_private(private, account)
    assert [rc for _argv, rc, _err in account.ran] == [0] * len(account.ran), account.ran
    assert not os.path.lexists(private / "run")
    assert stat.S_IMODE(os.lstat(private).st_mode) == 0o700


@darwin
def test_nested_flags_acls_and_modes_do_not_outlast_the_account_sequence(runs):
    """A: run/ is gone after one cleanup, so the next account run is not refused."""
    private = _private(runs)
    _lock_down(private / "run" / "home")
    account = _SameUser(runs)
    close_private(private, account)
    assert not os.path.lexists(private / "run"), account.ran
    assert [rc for _argv, rc, _err in account.ran] == [0, 0], account.ran
    assert stale_run_error(_Refusing(runs)) is None
    assert stat.S_IMODE(os.lstat(private).st_mode) == 0o700


def _run_printed(steps: str) -> list[tuple[list[str], int, str]]:
    """Run each printed step but root's: the account's without sudo and the allow-all wrapper."""
    ran = []
    for step in steps.split("; as "):
        who, _, command = step.partition(": ")
        if who.startswith("root"):
            continue
        argv = shlex.split(command)
        if who.endswith("verifier account"):
            assert argv[:5] == ["sudo", *SUDO[1:]]
            argv = argv[5:]
            if argv[0] == "/usr/bin/sandbox-exec":
                assert argv[1:3] == ["-p", "(version 1) (allow default)"]
                argv = argv[3:]
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        ran.append((argv, done.returncode, done.stderr.strip()))
    return ran


@darwin
def test_the_printed_recovery_steps_clear_the_same_tree(runs):
    """A and B: the steps an operator is told to run remove it, and each returns 0."""
    private = _private(runs)
    _lock_down(private / "run" / "home")
    message = stale_run_error(_Refusing(runs))
    assert message is not None
    ran = _run_printed(message[message.index("as the verifier account: ") :])
    assert not os.path.lexists(private / "run"), ran
    assert [rc for _argv, rc, _err in ran] == [0] * len(ran), ran
    assert stat.S_IMODE(os.lstat(private).st_mode) == 0o700
    assert stale_run_error(_Refusing(runs)) is None


@darwin
def test_the_account_sequence_does_not_follow_a_symlink_out_of_run(runs, tmp_path):
    """Cleanup must not follow links, including ones with a hiding ACL."""
    outside = tmp_path / "outside"
    (outside / "d").mkdir(parents=True)
    (outside / "d" / "kept").write_text("keep")
    (outside / "f").write_text("keep")
    _acl("everyone deny delete", str(outside / "f"))
    _ok(["/usr/bin/chflags", "uchg", str(outside / "f")])
    _acl("everyone deny delete_child", str(outside / "d"))
    os.chmod(outside / "d", 0o500)
    private = _private(runs)
    home = private / "run" / "home"
    for name, target in (("to-file", "f"), ("hidden-to-file", "f"), ("to-dir", "d")):
        (home / name).symlink_to(outside / target)
    _acl(HIDDEN, str(home / "hidden-to-file"))
    close_private(private, _SameUser(runs))
    assert not os.path.lexists(private / "run")
    for path in (outside / "f", outside / "d"):
        listing = subprocess.run(
            ["/bin/ls", "-ledO", str(path)], capture_output=True, text=True, check=True
        ).stdout
        assert "deny delete" in listing, listing
    assert (
        "uchg"
        in subprocess.run(
            ["/bin/ls", "-lO", str(outside / "f")], capture_output=True, text=True, check=True
        ).stdout
    )
    assert stat.S_IMODE(os.lstat(outside / "d").st_mode) == 0o500
    assert (outside / "d" / "kept").read_text() == "keep"


@darwin
def test_a_lock_below_path_max_is_cleared(runs):
    """Past PATH_MAX (1024) a tool given the full path gets ENAMETOOLONG; names stay relative."""
    assert sys.platform == "darwin"
    private = _private(runs)
    back = os.open(".", os.O_RDONLY)
    try:
        os.chdir(private / "run" / "home")
        for _ in range(6):
            os.mkdir("d" * 200)
            os.chdir("d" * 200)
        Path("f").write_text("x")
        _acl(HIDDEN, "f")
        os.chflags("f", stat.UF_IMMUTABLE)
    finally:
        os.fchdir(back)
        os.close(back)
    close_private(private, _SameUser(runs))
    assert not os.path.lexists(private / "run")


@darwin
@pytest.mark.parametrize("limits", [(3, 45), (500_000, 0)])
def test_at_a_limit_the_rest_stays_and_the_next_run_is_refused(runs, monkeypatch, limits):
    """At an entry or time limit, leave residue and report recovery steps."""
    monkeypatch.setattr(run_scrub, "_UNLOCK_LIMITS", limits)
    private = _private(runs)
    _lock_down(private / "run" / "home")
    account = _SameUser(runs)
    close_private(private, account)
    assert os.path.lexists(private / "run")
    assert account.ran[0][1] == 1 and "limit reached" in account.ran[0][2], account.ran
    assert stat.S_IMODE(os.lstat(private).st_mode) == 0o750
    message = stale_run_error(_Refusing(runs))
    assert message is not None and "as root, only if" in message


def _walk(monkeypatch, run: Path, limits: tuple[int, int]) -> subprocess.CompletedProcess:
    monkeypatch.setattr(run_scrub, "_UNLOCK_LIMITS", limits)
    argv = run_scrub._unlock_argv(sys.executable, os.path.realpath(run))
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def test_unwritable_top_is_reported_on_every_pass(runs):
    top = runs / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    denied = (
        f"real_chmod=os.chmod; real_access=os.access; "
        f"os.chmod=lambda p,m,**k: None if os.fspath(p)=={str(top)!r} "
        f"else real_chmod(p,m,**k); "
        f"os.access=lambda p,m,**k: False if os.fspath(p)=={str(top)!r} "
        "else real_access(p,m,**k)"
    )
    try:
        for _ in range(2):
            done = _walk_source(top, (2, 45), denied)
            assert done.returncode != 0
            assert str(top) in done.stderr and "not writable by the account" in done.stderr
            assert "limit reached" not in done.stderr
        refusal = run_scrub._hint(USER, sys.executable, top / "run")
        assert f"unlock: {top / 'run'} is not writable by the account" in refusal
    finally:
        os.chmod(top, 0o700)


def test_top_mode_0500_is_fixed_and_deep_tree_moves_up(runs):
    top = runs / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    os.chmod(top, 0o500)
    try:
        done = _walk_source(top, (2, 45))
        assert done.returncode == 1 and "limit reached" in done.stderr, done
        assert stat.S_IMODE(os.lstat(top).st_mode) == 0o700
        assert len(list(top.glob(".unlock-*"))) == 1
    finally:
        os.chmod(top, 0o700)


def test_writable_top_and_symlink_top_keep_existing_results(runs):
    top = runs / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    done = _walk_source(top, (2, 45))
    assert done.returncode == 1 and "limit reached" in done.stderr, done
    assert list(top.glob(".unlock-*"))
    assert "not writable by the account" not in done.stderr
    outside = runs / "outside"
    outside.mkdir()
    (outside / "kept").write_text("keep")
    link = runs / "link"
    link.symlink_to(outside, target_is_directory=True)
    done = _walk_source(link, (2, 45))
    assert done.returncode != 0 and "not a real directory" in done.stderr, done
    assert list(outside.iterdir()) == [outside / "kept"]


@darwin
def test_a_flag_that_lchflags_cannot_clear_is_reported_as_failure(runs):
    """The real walker must leave an unremovable flag for the caller to refuse, not count the
    file as completed merely because lchflags was attempted."""
    assert sys.platform == "darwin"  # chflags exists only there
    top = runs / "top"
    top.mkdir()
    locked = top / "locked"
    locked.write_text("x")
    os.chflags(locked, stat.UF_IMMUTABLE)
    preamble = """\
import os
real = os.lchflags
def denied(path, flags):
    if path == 'locked':
        raise PermissionError(1, 'injected flag refusal')
    return real(path, flags)
os.lchflags = denied
"""
    argv = [sys.executable, "-I", "-c", f"{preamble}\n{run_scrub._UNLOCK}"]
    done = subprocess.run(
        [*argv, os.path.realpath(top), *map(str, run_scrub._UNLOCK_LIMITS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1 and "limit reached" in done.stderr, done


@darwin
def test_a_large_directory_stops_at_the_entry_limit(runs, monkeypatch):
    """A large listing must stop at the entry budget."""
    private = _private(runs)
    crowd = private / "run" / "home" / "crowd"
    crowd.mkdir()
    for index in range(1000):
        (crowd / f"f{index}").write_text("x")
    done = _walk(monkeypatch, private / "run", (10, 45))
    assert done.returncode == 1 and "limit reached" in done.stderr, done


@darwin
def test_with_no_time_left_one_entry_is_still_unlocked(runs, monkeypatch):
    """A zero-second pass still unlocks one entry before stopping."""
    private = _private(runs)
    home = private / "run" / "home"
    for name in ("f", "g"):
        (home / name).write_text("x")
        _acl("everyone deny delete", str(home / name))
    done = _walk(monkeypatch, private / "run", (500_000, 0))
    assert done.returncode == 1 and "limit reached" in done.stderr, done
    listing = subprocess.run(
        ["/bin/ls", "-le", str(home)], capture_output=True, text=True, check=True
    ).stdout
    assert listing.count("deny delete") == 1, listing


def test_an_unexpected_error_in_cleanup_is_raised_not_swallowed(runs):
    """Expected cleanup failures ALERT; unexpected exceptions propagate fail-closed."""
    private = _private(runs)

    class _Broken(_Refusing):
        def remove(self, path):
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        close_private(private, _Broken(runs))
    assert os.path.lexists(private / "run")
    assert stat.S_IMODE(os.lstat(private).st_mode) == 0o750
    assert "Never raises" not in (run_scrub.close_private.__doc__ or "")
    close_private(private, _Refusing(runs))  # the expected failure: no raise
    assert not os.path.lexists(private / "run")
