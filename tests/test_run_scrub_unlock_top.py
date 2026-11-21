"""Top-directory preflight failures must be precise and safe."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from run_scrub_unlock_harness import walk_source

import cairnwork.run_scrub as run_scrub


def _lines(result) -> list[str]:
    return result.stderr.splitlines()


@pytest.mark.parametrize("mode", [0o000, 0o400])
def test_top_without_permissions_is_fixed_before_walking(tmp_path, mode):
    top = tmp_path / "top"
    (top / "a" / "b" / "c").mkdir(parents=True)
    os.chmod(top, mode)
    try:
        result = walk_source(top, (2, 45))
        assert result.returncode == 1 and "limit reached" in result.stderr, result
        assert stat.S_IMODE(os.lstat(top).st_mode) == 0o700
    finally:
        os.chmod(top, 0o700)


def test_missing_top_has_accurate_failure(tmp_path):
    top = tmp_path / "missing"
    result = walk_source(top, (10, 45))
    assert result.returncode != 0
    assert "not writable by the account" not in result.stderr
    assert "limit reached" not in result.stderr
    assert "does not exist" in result.stderr


def test_lstat_failure_has_accurate_failure(tmp_path):
    top = tmp_path / "top"
    top.mkdir()
    preamble = f"""\
real_lstat=os.lstat
def broken_lstat(path, *args, **kwargs):
    if os.fspath(path) == {str(top)!r}: raise PermissionError('injected lstat failure')
    return real_lstat(path, *args, **kwargs)
os.lstat=broken_lstat
"""
    result = walk_source(top, (10, 45), preamble)
    assert result.returncode != 0
    assert "not writable by the account" not in result.stderr
    assert "limit reached" not in result.stderr
    assert "could not inspect top" in result.stderr


def test_lchflags_failure_has_accurate_failure(tmp_path):
    top = tmp_path / "top"
    top.mkdir()
    preamble = f"""\
import os, stat
real_lstat=os.lstat
def flagged_lstat(path, *args, **kwargs):
    info=real_lstat(path, *args, **kwargs)
    if os.fspath(path) == {str(top)!r}: info.st_flags=1
    return info
os.lstat=flagged_lstat
os.lchflags=lambda *args: (_ for _ in ()).throw(PermissionError('injected lchflags failure'))
stat.UF_IMMUTABLE=1
stat.UF_APPEND=2
"""
    result = walk_source(top, (10, 45), preamble)
    assert result.returncode != 0
    assert "not writable by the account" not in result.stderr
    assert "limit reached" not in result.stderr
    assert "could not clear top flags" in result.stderr


def test_really_unwritable_top_stops_before_walker(tmp_path):
    top = tmp_path / "top"
    (top / "child").mkdir(parents=True)
    denied = (
        f"real_chmod=os.chmod; real_access=os.access; "
        f"os.chmod=lambda p,m,**k: None if os.fspath(p)=={str(top)!r} "
        f"else real_chmod(p,m,**k); "
        f"os.access=lambda p,m,**k: False if os.fspath(p)=={str(top)!r} "
        "else real_access(p,m,**k)"
    )
    result = walk_source(top, (2, 45), denied)
    assert result.returncode != 0
    assert "not writable by the account" in result.stderr
    assert "limit reached" not in result.stderr
    assert not list(top.glob(".unlock-*"))


def test_control_characters_in_reported_paths_are_neutralized(tmp_path):
    top = tmp_path / "top\n::warning::notice\n##[group]heading"
    top.mkdir()
    denied = (
        f"real_chmod=os.chmod; real_access=os.access; "
        f"os.chmod=lambda p,m,**k: None if os.fspath(p)=={str(top)!r} "
        f"else real_chmod(p,m,**k); "
        f"os.access=lambda p,m,**k: False if os.fspath(p)=={str(top)!r} "
        "else real_access(p,m,**k)"
    )
    result = walk_source(top, (2, 45), denied)
    assert result.returncode != 0
    assert not any(line.startswith("::warning::") for line in _lines(result))
    assert not any(line.startswith("##[group]") for line in _lines(result))


def test_control_characters_in_recovery_commands_are_neutralized(tmp_path):
    top = tmp_path / "top\n::warning::notice\n##[group]heading"
    message = run_scrub._hint("verifier", sys.executable, top)
    assert not any(line.startswith("::warning::") for line in message.splitlines())
    assert not any(line.startswith("##[group]") for line in message.splitlines())
