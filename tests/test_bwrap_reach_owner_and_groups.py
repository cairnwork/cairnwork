"""Reach decisions err toward 'reachable' when ownership or group membership cannot be proven."""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from cairnwork_sandbox import bwrap_reach

OPERATOR = 1000
OTHER_OWNER = 1234
GID = 1000


def _info(mode: int, uid: int, gid: int = GID) -> os.stat_result:
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


def test_a_dir_owned_by_a_possible_account_without_o_plus_x_is_enterable():
    assert bwrap_reach._may_enter("/x", _info(0o700, OTHER_OWNER)) is True


@pytest.mark.parametrize("uid", [0, OPERATOR])
def test_a_dir_owned_by_root_or_the_operator_stays_closed(uid):
    assert bwrap_reach._may_enter("/x", _info(0o700, uid)) is False


def test_the_owner_without_u_plus_x_is_not_enterable_by_that_rule():
    assert bwrap_reach._may_enter("/x", _info(0o600, OTHER_OWNER)) is False


def test_a_user_private_group_on_a_complete_table_stays_closed():
    assert bwrap_reach._may_enter("/x", _info(0o750, OPERATOR)) is False


def test_a_user_table_that_does_not_list_the_owner_counts_as_others(monkeypatch):
    monkeypatch.setattr(bwrap_reach.pwd, "getpwall", lambda: [_user(7, 7)])
    assert bwrap_reach._group_has_others(_info(0o750, OPERATOR)) is True


def test_an_unlistable_user_table_counts_as_others(monkeypatch):
    def boom():
        raise OSError("nss")

    monkeypatch.setattr(bwrap_reach.pwd, "getpwall", boom)
    assert bwrap_reach._group_has_others(_info(0o750, OPERATOR)) is True
