"""#240: the account-owned checkout (account_checkout.py), without sudo.

The copy helper runs here as the owner: symlinks stay symlinks, regular files keep bytes, mode
and times, other file types are refused before they are opened, an existing target is refused.
``make_account_checkout`` runs with a stand-in account whose "sudo" is a plain subprocess: the copy
must be a directory owned by the account and equal to the candidate tree, and the staging clone is
removed whatever happens. The real account path is in test_checkout_write_parity.py.
"""

import os
import pwd
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import git

from cairnwork.account_checkout import COPY_HELPER, SHARE_HELPER, STAGE, make_account_checkout
from cairnwork.verifier_account import VerifierAccount
from cairnwork.workspace import WorkspaceError


def _copy(source, target, timeout=20, prefix=""):
    return subprocess.run(  # noqa: S603 - the helper under test
        [sys.executable, "-I", "-S", "-c", prefix + COPY_HELPER, str(source), str(target)],
        capture_output=True, text=True, timeout=timeout, check=False,
    )  # fmt: skip


def test_copy_keeps_bytes_modes_times_and_symlinks(tmp_path):
    source, outside = tmp_path / "src", tmp_path / "outside"
    (source / "d").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret").write_text("s")
    (source / "f.txt").write_text("hello")
    (source / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(source / "run.sh", 0o750)
    os.chmod(source / "d", 0o751)
    os.utime(source / "f.txt", ns=(1_000_000_000_000_000_000, 1_100_000_000_000_000_000))
    (source / "to-outside").symlink_to(outside)
    (source / "dangling").symlink_to(tmp_path / "missing")
    done = _copy(source, tmp_path / "dst")
    assert done.returncode == 0, done.stderr
    dst = tmp_path / "dst"
    assert (dst / "f.txt").read_text() == "hello"
    assert os.lstat(dst / "f.txt").st_mtime_ns == 1_100_000_000_000_000_000
    assert stat.S_IMODE(os.lstat(dst / "run.sh").st_mode) == 0o750
    assert stat.S_IMODE(os.lstat(dst / "d").st_mode) == 0o751
    for name in ("to-outside", "dangling"):
        assert os.path.islink(dst / name) and os.readlink(dst / name) == os.readlink(source / name)
    assert not (dst / "secret").exists()  # the symlinked directory was not copied into dst


def test_copy_writes_every_byte_when_writes_are_short(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "f.bin").write_bytes(bytes(range(256)) * 41)
    short = "import os\n_write = os.write\nos.write = lambda fd, b: _write(fd, bytes(b[:2]))\n"
    done = _copy(source, tmp_path / "dst", prefix=short)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "dst" / "f.bin").read_bytes() == (source / "f.bin").read_bytes()


def _deep(root, depth):
    path = root
    for _ in range(depth):
        path = path / "d"
    path.mkdir(parents=True)
    (path / "leaf.txt").write_text("leaf")
    return path.relative_to(root)


@pytest.mark.parametrize("helper", ["copy", "share"])
def test_the_helpers_walk_any_depth(tmp_path, helper):
    """Iterative, not recursive: a tree deeper than the recursion limit is no error."""
    source = tmp_path / "src"
    leaf = _deep(source, 120)
    low = "import sys\nsys.setrecursionlimit(60)\n"
    if helper == "copy":
        done = _copy(source, tmp_path / "dst", prefix=low)
        assert done.returncode == 0, done.stderr[-300:]
        assert (tmp_path / "dst" / leaf / "leaf.txt").read_text() == "leaf"
    else:
        os.chmod(source / leaf / "leaf.txt", 0o600)
        done = _share(source, prefix=low)
        assert done.returncode == 0, done.stderr[-300:]
        assert stat.S_IMODE(os.lstat(source / leaf / "leaf.txt").st_mode) == 0o644


def _share(checkout, prefix=""):
    return subprocess.run(  # noqa: S603 - the helper under test
        [sys.executable, "-I", "-S", "-c", prefix + SHARE_HELPER, str(checkout)],
        capture_output=True, text=True, timeout=20, check=False,
    )  # fmt: skip


def test_share_gives_group_and_other_exactly_what_the_owner_may_read(tmp_path):
    """Group and other r/x become the user's r/x, whatever they were; other bits stay. A 0600 file
    and a 0700 directory become readable; a path closed to its owner stays closed, also when group
    and other could read it (0044, 0077: closed to them too); symlinks (to a file or to a directory
    outside) are neither followed nor entered, and the top-level .git is left alone. A file with a
    second link outside, which would need a change, is left as it is and the helper fails."""
    root, outside = tmp_path / "co", tmp_path / "outside"
    outdir = tmp_path / "outdir"  # stands in for an account-owned directory outside the copy
    outdir.mkdir(mode=0o700)
    (outdir / "secret").write_text("s")
    os.chmod(outdir / "secret", 0o600)
    (tmp_path / "hl-outside").write_text("h")
    os.chmod(tmp_path / "hl-outside", 0o600)
    (root / "d700").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "sub" / ".git").mkdir(parents=True)
    outside.write_text("o")
    os.chmod(outside, 0o600)
    modes = {
        "f600": 0o600, "f700": 0o700, "f000": 0, "f200": 0o200, "d700/f640": 0o640,
        "f044": 0o044, "f620": 0o620, "f755": 0o755,
    }  # fmt: skip
    for name, mode in modes.items():
        (root / name).write_text("x")
        os.chmod(root / name, mode)
    (root / "link").symlink_to(outside)
    (root / "dirlink").symlink_to(outdir, target_is_directory=True)
    os.link(tmp_path / "hl-outside", root / "hl600")
    (root / ".git" / "config").write_text("c")
    os.chmod(root / ".git" / "config", 0o600)
    (root / "sub" / ".git" / "f").write_text("s")
    os.chmod(root / "sub" / ".git" / "f", 0o600)
    os.chmod(root / "d700", 0o700)
    (root / "d000").mkdir(mode=0)
    (root / "d077").mkdir(mode=0o700)
    os.chmod(root / "d077", 0o077)
    try:
        done = _share(root)
    finally:
        modes_after = {}
        for name in ("d000", "d077", "d700"):  # read, then reopen: pytest's tmp cleanup must get in
            modes_after[name] = stat.S_IMODE(os.lstat(root / name).st_mode)
            os.chmod(root / name, 0o700)
    assert done.returncode == 1 and done.stderr.strip() == "more than one link: hl600"
    expected = {
        "f600": 0o644, "f700": 0o755, "f000": 0, "f200": 0o200, "d700/f640": 0o644,
        "f044": 0, "f620": 0o664, "f755": 0o755, "d700": 0o755, "d000": 0, "d077": 0o022,
        ".git/config": 0o600, "sub/.git/f": 0o644, "hl600": 0o600,
    }  # fmt: skip
    for name in expected:
        modes_after.setdefault(name, stat.S_IMODE(os.lstat(root / name).st_mode))
    assert {name: modes_after[name] for name in expected} == expected
    outside_modes = {
        p: stat.S_IMODE(os.lstat(p).st_mode) for p in (outside, outdir, outdir / "secret")
    }
    assert outside_modes == {outside: 0o600, outdir: 0o700, outdir / "secret": 0o600}
    assert stat.S_IMODE(os.lstat(tmp_path / "hl-outside").st_mode) == 0o600


def test_share_never_walks_out_through_a_directory_swapped_mid_walk(tmp_path):
    """A directory replaced by a symlink to an outside directory after the helper saw it (here by
    the helper's own stat, standing in for a process that should not exist): its chmod does not
    follow the link and the walk does not enter it, so nothing outside changes."""
    root, outdir = tmp_path / "co", tmp_path / "outdir"
    outdir.mkdir(mode=0o700)
    (outdir / "secret").write_text("s")
    os.chmod(outdir / "secret", 0o600)
    (root / "sub").mkdir(parents=True, mode=0o700)
    (root / "sub" / "f").write_text("x")
    os.chmod(root / "sub" / "f", 0o600)
    swap = (
        "import os, sys\nSWAPPED = []\n"
        "def _swapping(real):\n"
        "    def stat(path, *args, **kwargs):\n"
        "        info = real(path, *args, **kwargs)\n"
        "        if os.path.basename(str(path)) == 'sub' and not SWAPPED:\n"
        "            SWAPPED.append(1)\n"
        "            os.rename(sys.argv[1] + '/sub', sys.argv[1] + '/sub.real')\n"
        f"            os.symlink({str(outdir)!r}, sys.argv[1] + '/sub')\n"
        "        return info\n"
        "    return stat\n"
        "os.stat, os.lstat = _swapping(os.stat), _swapping(os.lstat)\n"
    )
    done = _share(root, prefix=swap)
    assert "Traceback" not in done.stderr, done.stderr
    assert (root / "sub").is_symlink()
    assert stat.S_IMODE(os.lstat(outdir).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(outdir / "secret").st_mode) == 0o600


def test_the_owner_rmtree_never_follows_a_symlink_out():
    assert shutil.rmtree.avoids_symlink_attacks  # what remove_checkout's fallback relies on


def test_copy_refuses_a_fifo_without_opening_it(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    os.mkfifo(source / "pipe")
    done = _copy(source, tmp_path / "dst")  # an open() would block until the timeout
    assert done.returncode != 0 and "refused file type" in done.stderr


def test_copy_refuses_an_existing_target(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    done = _copy(tmp_path / "src", tmp_path / "dst")
    assert done.returncode != 0 and "target exists" in done.stderr


class _LocalAccount:
    """``wrap`` runs argv directly, so the copy is made by (and owned by) the test's own uid."""

    grant_read = staticmethod(VerifierAccount.grant_read)

    def __init__(self, user, root, tamper=""):
        self.user, self.root, self.tamper, self.killed = user, root, tamper, 0

    def wrap(self, argv, profile, env=None):
        assert profile is None and argv[1:4] == ["-I", "-S", "-c"]
        return [*argv[:4], argv[4] + self.tamper, *argv[5:]]

    def kill_all(self):
        self.killed += 1
        return 0


def _make(repo, tmp_path, account):
    work = tmp_path / "w.work"
    work.mkdir()
    commit = git(repo, "rev-parse", "HEAD")
    return work, lambda: make_account_checkout(account, sys.executable, repo, commit, work, "w")


ME = pwd.getpwuid(os.getuid()).pw_name


def test_the_copy_is_the_candidate_tree_and_the_staging_clone_goes(repo, tmp_path):
    work, make = _make(repo, tmp_path, _LocalAccount(ME, tmp_path))
    checkout = make()
    assert checkout == work / "w" and (checkout / "src" / "a.py").read_text() == "x = 1\n"
    assert (checkout / ".git").is_dir()
    assert not (work / STAGE).exists()


def test_a_copy_not_owned_by_the_account_is_refused(repo, tmp_path):
    other = next(p.pw_name for p in pwd.getpwall() if p.pw_uid not in (0, os.getuid()))
    work, make = _make(repo, tmp_path, _LocalAccount(other, tmp_path))
    with pytest.raises(WorkspaceError, match="not a directory owned by"):
        make()
    assert not (work / STAGE).exists()


def test_a_copy_that_differs_from_the_candidate_is_refused(repo, tmp_path):
    tamper = "\nopen(os.path.join(target, 'src', 'a.py'), 'a').write('y = 2\\n')\n"
    work, make = _make(repo, tmp_path, _LocalAccount(ME, tmp_path, tamper))
    with pytest.raises(WorkspaceError, match="differs from"):
        make()
    assert not (work / STAGE).exists()


def test_a_failed_copy_is_an_error_and_the_staging_clone_goes(repo, tmp_path):
    work, make = _make(repo, tmp_path, _LocalAccount(ME, tmp_path, "\nsys.exit('boom')\n"))
    with pytest.raises(WorkspaceError, match="account copy failed"):
        make()
    assert not (work / STAGE).exists()
    assert Path(work).is_dir()  # the work dir itself is the caller's to remove


def test_a_copy_target_swapped_for_a_symlink_is_refused(repo, tmp_path):
    """lstat, not stat: a symlink to a directory the account owns is not the copy."""
    tamper = "\nos.rename(target, target + '.real')\nos.symlink(target + '.real', target)\n"
    work, make = _make(repo, tmp_path, _LocalAccount(ME, tmp_path, tamper))
    with pytest.raises(WorkspaceError, match="not a directory owned by"):
        make()


def test_a_copy_that_times_out_ends_the_helper(repo, tmp_path, monkeypatch):
    import cairnwork.account_checkout as module

    monkeypatch.setattr(module, "COPY_TIMEOUT_SECONDS", 1)
    account = _LocalAccount(ME, tmp_path, "\nimport time\ntime.sleep(30)\n")
    work, make = _make(repo, tmp_path, account)
    with pytest.raises(WorkspaceError, match="account copy failed"):
        make()
    assert account.killed == 1  # kill_all: subprocess.run ended only the outer process
    assert not (work / STAGE).exists()


def _differences(monkeypatch, tmp_path, account, share):
    import cairnwork.account_checkout as module

    ran = []

    def as_account(*args):
        ran.append(args)
        return share()

    monkeypatch.setattr(module, "_as_account", as_account)
    monkeypatch.setattr(module, "_same_directory", lambda *_args: True)
    monkeypatch.setattr(module, "tree_differences", lambda *_args: ["changed pkg/x"])
    found = module.checkout_differences(tmp_path, "c", tmp_path, None, account, sys.executable)
    return found, ran


def _timed_out():
    raise subprocess.TimeoutExpired("share", 120)


@pytest.mark.parametrize(
    "share",
    [
        _timed_out,
        lambda: subprocess.CompletedProcess([], 1, "", "more than one link: pkg/x"),
    ],
)
def test_a_failing_share_step_fails_the_run_not_an_error(monkeypatch, tmp_path, share):
    """The share step's failure (its timeout, or its own exit 1) is never raised past the verdict
    and never lets the comparison run on a copy left partly shared: the run fails."""
    found, ran = _differences(monkeypatch, tmp_path, _LocalAccount(ME, tmp_path), share)
    assert len(ran) == 1 and len(found) == 1
    assert found[0].startswith("checkout not shared with the owner: share step failed")


@pytest.mark.parametrize("left", [1, -1])
def test_no_share_step_while_account_processes_may_be_left(monkeypatch, tmp_path, left):
    """kill_all must confirm that no process of the account is left (none, not "unknown") before
    the share step runs unconfined on the copy; otherwise it never runs and the run fails."""
    account = _LocalAccount(ME, tmp_path)
    account.kill_all = lambda: left
    found, ran = _differences(
        monkeypatch, tmp_path, account, lambda: subprocess.CompletedProcess([], 0, "", "")
    )
    assert ran == []
    assert found == (
        "checkout not shared with the owner: the account's processes could not be confirmed ended",
    )


def test_a_shared_copy_is_compared(monkeypatch, tmp_path):
    account = _LocalAccount(ME, tmp_path)
    found, ran = _differences(
        monkeypatch, tmp_path, account, lambda: subprocess.CompletedProcess([], 0, "", "")
    )
    assert account.killed == 1 and len(ran) == 1 and found == ("changed pkg/x",)
