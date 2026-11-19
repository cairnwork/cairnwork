"""Regression coverage for descriptor-based credential-link scans and outside target hops."""

import errno
import os

import pytest

from cairnwork_sandbox import bwrap, bwrap_inner
from cairnwork_sandbox.backend import SandboxError


def _real(path):
    return os.path.realpath(path)


def _policy(home):
    return bwrap.BACKEND.policy((), (), home=home)


def _null_bound(policy, target):
    lines = policy.text.splitlines()
    return any(
        lines[index : index + 3] == ["--ro-bind", "/dev/null", _real(target)]
        for index in range(len(lines) - 2)
    )


def _layout(tmp_path):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("secret")
    return home, outside, secret


def _race_hook(monkeypatch, victim, action):
    real_open, real_scandir = os.open, os.scandir
    fired = False

    def fire(value):
        nonlocal fired
        if not fired and (str(value).endswith(victim) or str(value) == victim):
            fired = True
            action()

    def open_(path, flags, *args, **kwargs):
        fire(path)
        return real_open(path, flags, *args, **kwargs)

    def scandir(path):
        fire(path)
        return real_scandir(path)

    monkeypatch.setattr(bwrap_inner.os, "open", open_)
    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    return lambda: fired


def test_parent_replaced_with_symlink_is_rescanned_or_refused(tmp_path, monkeypatch):
    home, outside, secret = _layout(tmp_path)
    victim = home / ".aws" / "a"
    victim.mkdir()
    (victim / "l").symlink_to(secret)
    innocuous = tmp_path / "innocuous"
    innocuous.mkdir()

    def replace():
        victim.rename(home / ".aws" / "a2")
        victim.symlink_to(innocuous)

    fired = _race_hook(monkeypatch, "a", replace)
    try:
        policy = _policy(home)
    except SandboxError:
        pass
    else:
        assert _null_bound(policy, secret)
    assert fired()


def test_one_rename_restarts_whole_scan_and_finds_moved_link(tmp_path, monkeypatch):
    home, outside, secret = _layout(tmp_path)
    victim = home / ".aws" / "a"
    victim.mkdir()
    (victim / "l").symlink_to(secret)
    fired = _race_hook(monkeypatch, "a", lambda: victim.rename(home / ".aws" / "a2"))
    policy = _policy(home)
    assert fired()
    assert _null_bound(policy, secret)


def test_repeated_renames_fail_after_scan_attempt_limit(tmp_path, monkeypatch):
    home, _, _ = _layout(tmp_path)
    a = home / ".aws" / "a"
    a.mkdir()
    (a / "l").symlink_to(tmp_path / "missing")
    calls = 0
    real_open = os.open

    def open_(path, flags, *args, **kwargs):
        nonlocal calls
        if str(path) == "a":
            calls += 1
            a.rename(home / ".aws" / f"moved{calls}")
            try:
                return real_open(path, flags, *args, **kwargs)
            except FileNotFoundError:
                a.mkdir()
                (a / "l").symlink_to(tmp_path / "missing")
                raise
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(bwrap_inner.os, "open", open_)
    with pytest.raises(SandboxError, match="changing"):
        _policy(home)
    assert calls == bwrap_inner.SCAN_ATTEMPTS


def test_eacces_opening_child_refuses_without_retry(tmp_path, monkeypatch):
    home, _, _ = _layout(tmp_path)
    (home / ".aws" / "a").mkdir()
    calls = 0
    real_open = os.open

    def open_(path, flags, *args, **kwargs):
        nonlocal calls
        if str(path) == "a":
            calls += 1
            raise PermissionError(errno.EACCES, "denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(bwrap_inner.os, "open", open_)
    with pytest.raises(SandboxError, match="cannot scan"):
        _policy(home)
    assert calls == 1


def _scandir_hook(monkeypatch, victim, action):
    """Fire ``action`` once, after the victim's fd is open and just before it is listed."""
    real_scandir, fired, ident = os.scandir, [], os.stat(victim)

    def scandir(fd):
        if not fired and isinstance(fd, int) and os.path.samestat(os.fstat(fd), ident):
            fired.append(1)
            action()
        return real_scandir(fd)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    return fired


@pytest.mark.parametrize("swap", ["rename", "to_link"])
@pytest.mark.parametrize("depth,parent", [(1, False), (2, False), (2, True)])
def test_swap_between_open_and_listing_never_leaves_the_secret_unmasked(
    tmp_path, monkeypatch, swap, depth, parent
):
    home, _, secret = _layout(tmp_path)
    top = home / ".aws" / "a"
    victim = top if depth == 1 else top / "b"
    victim.mkdir(parents=True)
    (victim / "l").symlink_to(secret)
    empty = tmp_path / "empty"
    empty.mkdir()

    moved = top if (depth == 2 and parent) else victim

    def action():
        moved.rename(moved.parent / "moved")
        if swap == "to_link":
            moved.symlink_to(empty)

    fired = _scandir_hook(monkeypatch, victim, action)
    try:
        policy = _policy(home)
    except SandboxError:
        pass
    else:
        assert _null_bound(policy, secret)
    assert fired


def test_many_subdirectories_do_not_exhaust_descriptors(tmp_path):
    import resource

    home, _, _ = _layout(tmp_path)
    for i in range(400):
        (home / ".aws" / f"d{i}").mkdir()
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
    try:
        _policy(home)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_replacement_made_between_listing_and_open_is_rescanned(tmp_path, monkeypatch):
    home, _, secret = _layout(tmp_path)
    victim = home / ".aws" / "a"
    victim.mkdir()
    (victim / "l").symlink_to(secret)

    def swap():
        victim.rename(home / ".aws" / "a2")
        victim.mkdir()  # a different directory under the same name

    fired = _race_hook(monkeypatch, "a", swap)
    policy = _policy(home)
    assert fired()
    assert _null_bound(policy, secret)


def test_subtree_moved_out_of_the_tree_during_the_scan_refuses(tmp_path, monkeypatch):
    home, outside, secret = _layout(tmp_path)
    victim = home / ".aws" / "a"
    victim.mkdir()
    (victim / "l").symlink_to(secret)
    elsewhere = home / ".aws2"
    elsewhere.mkdir()
    _race_hook(monkeypatch, "a", lambda: victim.rename(elsewhere / "a"))
    with pytest.raises(SandboxError, match="changing"):
        _policy(home)


def test_a_mount_point_below_the_credential_directory_refuses(tmp_path, monkeypatch):
    base = tmp_path / "cred"
    (base / "sub").mkdir(parents=True)
    real = os.fstat

    def fstat(fd):
        st = real(fd)
        if fd != root_fd and not st.st_mode & 0o100000:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1, *tuple(st)[3:]))
        return st

    root_fd = -1
    real_open = os.open

    def open_(path, flags, *args, **kwargs):
        nonlocal root_fd
        fd = real_open(path, flags, *args, **kwargs)
        root_fd = fd if root_fd == -1 else root_fd
        return fd

    monkeypatch.setattr(bwrap_inner.os, "open", open_)
    monkeypatch.setattr(bwrap_inner.os, "fstat", fstat)
    with pytest.raises(SandboxError, match="mount point"):
        bwrap_inner._links_in(str(base))
