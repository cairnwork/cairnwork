"""Pins for the account-mode reach rule (bwrap_reach) that the other reach tests leave open: the
group-execute bit, listed group members, the ACL check itself, and non-directory path parts.
True means "the account may enter", so the path is masked; none of these may fix a missed mask."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork_sandbox import bwrap_reach

OPERATOR = 1000
OTHER_OWNER = 1234
GID = 1000


def _info(mode: int, uid: int = OPERATOR, gid: int = GID) -> os.stat_result:
    return os.stat_result((stat.S_IFDIR | mode, 1, 1, 1, uid, gid, 0, 0, 0, 0))


def _user(uid: int, gid: int = GID, name: str = "u") -> SimpleNamespace:
    return SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_name=name)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: OPERATOR)
    monkeypatch.setattr(bwrap_reach, "_has_acl", lambda path: False)
    monkeypatch.setattr(bwrap_reach.grp, "getgrgid", lambda gid: SimpleNamespace(gr_mem=[]))
    monkeypatch.setattr(bwrap_reach.pwd, "getpwuid", lambda uid: _user(uid, name=f"n{uid}"))
    monkeypatch.setattr(
        bwrap_reach.pwd, "getpwall", lambda: [_user(OPERATOR), _user(OTHER_OWNER, 5)]
    )


def _group_with(monkeypatch, members: list[str], primary_users: list[int] | None = None):
    monkeypatch.setattr(
        bwrap_reach.grp, "getgrgid", lambda gid: SimpleNamespace(gr_mem=list(members))
    )
    users = [_user(OPERATOR), *[_user(uid, GID) for uid in primary_users or []]]
    monkeypatch.setattr(bwrap_reach.pwd, "getpwall", lambda: users)


def test_g_plus_x_with_another_user_in_the_group_is_enterable(monkeypatch):
    _group_with(monkeypatch, [], primary_users=[OTHER_OWNER])
    assert bwrap_reach._may_enter("/x", _info(0o710)) is True


@pytest.mark.parametrize("mode", [0o720, 0o740, 0o760, 0o700])
def test_a_group_bit_other_than_execute_does_not_open_the_directory(monkeypatch, mode):
    _group_with(monkeypatch, [], primary_users=[OTHER_OWNER])
    assert bwrap_reach._may_enter("/x", _info(mode)) is False


def test_a_listed_group_member_who_is_not_the_owner_counts_as_others(monkeypatch):
    _group_with(monkeypatch, ["someone"])
    assert bwrap_reach._group_has_others(_info(0o750)) is True


def test_a_group_listing_only_the_owner_is_not_others(monkeypatch):
    _group_with(monkeypatch, [f"n{OPERATOR}"])
    assert bwrap_reach._group_has_others(_info(0o750)) is False


def test_an_unknown_group_counts_as_others(monkeypatch):
    def unknown(gid: int):
        raise KeyError(gid)

    monkeypatch.setattr(bwrap_reach.grp, "getgrgid", unknown)
    assert bwrap_reach._group_has_others(_info(0o750)) is True


def _xattrs(monkeypatch, result):
    def listxattr(path, *, follow_symlinks=True):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.undo()  # these tests want the real _has_acl, not the autouse stub
    monkeypatch.setattr(os, "listxattr", listxattr, raising=False)


def test_an_access_acl_on_a_closed_directory_makes_it_enterable(monkeypatch):
    _xattrs(monkeypatch, ["user.x", "system.posix_acl_access"])
    assert bwrap_reach._has_acl("/x") is True
    monkeypatch.setattr(os, "geteuid", lambda: OPERATOR)
    assert bwrap_reach._may_enter("/x", _info(0o700, uid=0)) is True


@pytest.mark.parametrize("names", [[], ["user.x"], ["system.posix_acl_default"]])
def test_xattrs_without_an_access_acl_are_not_an_acl(monkeypatch, names):
    _xattrs(monkeypatch, names)
    assert bwrap_reach._has_acl("/x") is False


@pytest.mark.parametrize("code", [errno.ENOTSUP, errno.EOPNOTSUPP])
def test_a_filesystem_without_xattrs_is_not_an_acl(monkeypatch, code):
    _xattrs(monkeypatch, OSError(code, os.strerror(code)))
    assert bwrap_reach._has_acl("/x") is False


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.ENOENT])
def test_an_unreadable_xattr_list_counts_as_an_acl(monkeypatch, code):
    _xattrs(monkeypatch, OSError(code, os.strerror(code)))
    assert bwrap_reach._has_acl("/x") is True


def test_a_regular_file_or_a_symlink_part_is_not_traversable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bwrap_reach, "_may_enter", lambda path, info: True)  # only the type counts
    real = tmp_path / "real"
    real.mkdir()
    (real / "f").write_text("x")
    (tmp_path / "link").symlink_to(real)
    assert bwrap_reach.account_may_traverse(str(real)) is True
    assert bwrap_reach.account_may_traverse(str(real / "f")) is False
    assert bwrap_reach.account_may_traverse(str(tmp_path / "link")) is False
    assert bwrap_reach.account_may_traverse(str(tmp_path / "link" / "x")) is False
