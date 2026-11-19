"""Outside link targets are scanned too (#427)."""

import os

import pytest

from cairnwork_sandbox import bwrap, bwrap_inner
from cairnwork_sandbox.backend import SandboxError


def _policy(home):
    return bwrap.BACKEND.policy((), (), home=home)


def _null_bound(policy, target):
    lines = policy.text.splitlines()
    want = ["--ro-bind", "/dev/null", os.path.realpath(target)]
    return any(lines[i : i + 3] == want for i in range(len(lines) - 2))


def _layout(tmp_path):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("secret")
    return home, outside, secret


def test_chain_of_outside_directories_is_masked_to_the_end(tmp_path):
    home, outside, secret = _layout(tmp_path)
    first, second, third = (outside / n for n in "abc")
    for d in (first, second, third):
        d.mkdir()
    (home / ".aws" / "p").symlink_to(first)
    (first / "next").symlink_to(second)
    (second / "next").symlink_to(third)
    (third / "cred").symlink_to(secret)
    assert _null_bound(_policy(home), secret)


def test_link_cycles_between_outside_directories_terminate(tmp_path):
    home, outside, secret = _layout(tmp_path)
    first, second = outside / "d", outside / "e"
    first.mkdir()
    second.mkdir()
    (home / ".aws" / "p").symlink_to(first)
    (first / "back").symlink_to(second)
    (second / "back").symlink_to(first)
    (first / "cred").symlink_to(secret)
    policy = _policy(home)
    assert policy.text.splitlines().count(os.path.realpath(first)) == 1
    assert _null_bound(policy, secret)


def test_links_inside_the_scanned_directory_add_no_mask(tmp_path):
    home, _, _ = _layout(tmp_path)
    (home / ".aws" / "d").mkdir()
    (home / ".aws" / "d" / "in").symlink_to(home / ".aws")
    assert not _policy(home).text.count("/dev/null")


def test_too_many_scan_roots_are_refused(tmp_path):
    home, _, _ = _layout(tmp_path)
    for i in range(bwrap_inner.SCAN_BASES + 2):
        (tmp_path / f"out{i}").mkdir()
        (home / ".aws" / f"l{i}").symlink_to(tmp_path / f"out{i}")
    with pytest.raises(SandboxError, match="scan roots"):
        _policy(home)
