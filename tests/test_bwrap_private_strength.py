"""Strength pins for ``bwrap_private`` found by a hand mutation sweep: three behaviours the
existing policy-text tests ran through but did not assert (a dangling /etc link is not put back,
an unreadable link is skipped without failing the build, the default link cap)."""

# ruff: noqa: F811, F401
import os

import pytest

from cairnwork_sandbox import bwrap_private
from cairnwork_sandbox.backend import SandboxError
from tests.test_bwrap_private_trees import _at, _policy, _real, host


def test_a_dangling_etc_link_into_run_is_not_bound(host):
    (host / "etc" / "gone.conf").symlink_to(host / "run" / "gone")
    lines = _policy(host)
    assert _real(host / "run" / "gone") not in lines
    assert _at(lines, "--ro-bind", _real(host / "run" / "gone"), _real(host / "run" / "gone")) == -1


def test_a_link_that_cannot_be_read_is_skipped_not_fatal(host, monkeypatch):
    target = host / "tmp" / "t"
    target.write_text("")
    (host / "tmp" / "bad").symlink_to(target)
    (host / "tmp" / "ok").symlink_to(target)
    real_readlink = os.readlink

    def readlink(path, *args, **kwargs):
        if os.fspath(path).endswith("/bad"):
            raise PermissionError(13, "denied", os.fspath(path))
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(bwrap_private.os, "readlink", readlink)
    lines = _policy(host)
    tmp = _real(host / "tmp")
    assert _at(lines, "--symlink", _real(target), f"{tmp}/ok") >= 0
    assert _at(lines, "--symlink", _real(target), f"{tmp}/bad") == -1


def test_the_default_link_cap_is_4000(host):
    (host / "tmp" / "t").write_text("")
    for i in range(4001):
        (host / "tmp" / f"l{i}").symlink_to(host / "tmp" / "t")
    with pytest.raises(SandboxError, match="more than 4000 links"):
        _policy(host)
    (host / "tmp" / "l4000").unlink()
    assert sum(1 for line in _policy(host) if line == "--symlink") == 4000
