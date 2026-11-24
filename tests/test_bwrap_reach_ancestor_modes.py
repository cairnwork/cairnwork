"""Ancestor-directory permission check in the account-mode reach rule. A directory whose owner
bits are tighter than its other bits shuts the owner out of what is below it while the account may
still pass; the rule must read the ancestor's mode, not whether the owner's own stat of the next
part succeeds."""

import errno
import os

import pytest
from fixture_tree import world_dir

from cairnwork_sandbox import bwrap, bwrap_reach
from cairnwork_sandbox.backend import SandboxError


@pytest.fixture
def world():
    with world_dir("cw-anc-") as base:
        for name in ("home", "out", "other"):
            (base / name).mkdir(mode=0o755)
        (base / "out" / "d" / "sub").mkdir(parents=True, mode=0o755)
        (base / "out" / "d" / "sub" / "tok").write_text("x\n")
        (base / "home" / ".aws").mkdir()
        (base / "home" / ".aws" / "x").symlink_to(base / "out" / "d" / "sub" / "tok")
        try:
            yield base
        finally:
            (base / "out" / "d").chmod(0o755)


def _owner_stat_denied_below(monkeypatch, parent, exact=False):
    """Make the owner's lstat of anything below ``parent`` (or ``parent`` itself, ``exact``) fail
    EACCES, as on Linux."""
    real = os.lstat

    def lstat(path, *a, **kw):
        if str(path) == str(parent) if exact else str(path).startswith(str(parent) + os.sep):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(bwrap_reach.os, "lstat", lstat)


@pytest.mark.parametrize(("mode", "expect"), [(0o755, True), (0o701, True), (0o700, False)])
def test_a_denied_owner_stat_below_an_ancestor_follows_the_ancestors_mode(
    world, monkeypatch, mode, expect
):
    d = (world / "out" / "d").resolve()
    d.chmod(mode)
    _owner_stat_denied_below(monkeypatch, d)
    assert bwrap_reach.account_may_traverse(str(d / "sub")) is expect


@pytest.mark.parametrize("mode", [0o601, 0o611, 0o001])
def test_an_owner_shut_out_of_an_enterable_ancestor_refuses_the_policy(world, mode):
    d = world / "out" / "d"
    d.chmod(mode)
    if os.access(d / "sub", os.F_OK):
        pytest.skip("running as a user that stats through the directory (root)")
    with pytest.raises(SandboxError, match="cannot be checked"):
        bwrap.BACKEND.policy(
            (world / "other",), (), home=world / "home", mask_home_credentials=False
        )


@pytest.mark.parametrize(
    ("code", "expect"),
    [
        (errno.ENOENT, False),
        (errno.ENOTDIR, False),
        (errno.ELOOP, False),
        (errno.EIO, False),
        (errno.EACCES, True),
        (errno.EPERM, True),
    ],
)
def test_other_stat_errors_below_an_enterable_ancestor_stay_unreachable(
    world, monkeypatch, code, expect
):
    d = (world / "out" / "d").resolve()
    real = os.lstat

    def lstat(path, *a, **kw):
        if str(path).startswith(str(d) + os.sep):
            raise OSError(code, os.strerror(code), str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(bwrap_reach.os, "lstat", lstat)
    assert bwrap_reach.account_may_traverse(str(d / "sub")) is expect


def test_an_ancestor_closed_to_everyone_stays_unreachable_and_is_skipped(world):
    d = world / "out" / "d"
    d.chmod(0)
    if os.access(d / "sub", os.F_OK):
        pytest.skip("running as a user that stats through a mode-0 directory (root)")
    policy = bwrap.BACKEND.policy(
        (world / "other",), (), home=world / "home", mask_home_credentials=False
    )
    assert not [ln for ln in policy.text.split("\n") if ln.startswith(str(world / "out"))]


def test_a_denied_first_part_has_no_enterable_parent_before_it(world, monkeypatch):
    """Nothing was shown enterable above the first part, so a denial there is not reachable."""
    d = (world / "out" / "d").resolve()
    _owner_stat_denied_below(monkeypatch, "/", exact=True)
    assert bwrap_reach.account_may_traverse(str(d / "sub")) is False
    monkeypatch.chdir(world)
    _owner_stat_denied_below(monkeypatch, "out", exact=True)
    assert bwrap_reach.account_may_traverse("out/d/sub") is False
