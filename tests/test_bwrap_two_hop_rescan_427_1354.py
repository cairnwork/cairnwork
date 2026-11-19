"""Outside link targets are scanned too (#427); a subdirectory that changes mid-scan restarts the
scan a bounded number of times (#1354)."""

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


def _rename_once_on_scan(monkeypatch, victim, moved):
    real, calls = os.scandir, []

    def scandir(path):
        if str(path) == str(victim) and not calls:
            calls.append(1)
            victim.rename(moved)
        return real(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    return calls


def test_one_rename_restarts_the_scan_and_finds_the_moved_link(tmp_path, monkeypatch):
    home, _, secret = _layout(tmp_path)
    victim = home / ".aws" / "a"
    victim.mkdir()
    (victim / "l").symlink_to(secret)
    calls = _rename_once_on_scan(monkeypatch, victim, home / ".aws" / "a2")
    policy = _policy(home)
    assert calls
    assert _null_bound(policy, secret)


def test_a_tree_that_keeps_changing_is_refused_after_the_attempt_limit(tmp_path, monkeypatch):
    home, _, secret = _layout(tmp_path)
    victim = home / ".aws" / "a"
    victim.mkdir()
    (victim / "l").symlink_to(secret)
    real, calls = os.scandir, []

    def scandir(path):
        if str(path) == str(victim):
            calls.append(1)
            victim.rename(home / ".aws" / f"moved{len(calls)}")
            victim.mkdir()  # the name is back for the next pass
            raise FileNotFoundError(2, "gone")
        return real(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    with pytest.raises(SandboxError, match="kept changing"):
        _policy(home)
    assert len(calls) == bwrap_inner.SCAN_ATTEMPTS
