"""A git failure while checking a mirror's fetch refspec is a refusal, not "not applicable"
(Issue #1106). Only `git config` exit 1 (key unset) still means a local checkout."""

import subprocess

import pytest

from scripts import env_smoke
from scripts.env_smoke import _mirror_multi_branch_reason

_REAL_RUN = subprocess.run


def _fail(sub: str, code: int):
    def fake_run(args, **kwargs):
        if sub in args:
            return subprocess.CompletedProcess(args, code, "", "fatal: boom")
        return _REAL_RUN(args, **kwargs)

    return fake_run


def _set_main_refspec(repo):
    _REAL_RUN(
        [
            "git",
            "-C",
            str(repo),
            "config",
            "remote.origin.fetch",
            "+refs/heads/main:refs/remotes/origin/main",
        ],  # fmt: skip
        check=True,
    )


def test_broken_git_config_is_refused(repo):
    # A real git failure, no mocks: an unparsable .git/config makes `git config` exit non-zero.
    with (repo / ".git" / "config").open("a") as f:
        f.write("[broken\n")
    reason = _mirror_multi_branch_reason(repo)
    assert reason is not None
    assert "could not read remote.origin.fetch (git exit" in reason


@pytest.mark.parametrize("code", [3, 128])
def test_git_config_error_exit_is_refused(repo, monkeypatch, code):
    monkeypatch.setattr(env_smoke.subprocess, "run", _fail("config", code))
    reason = _mirror_multi_branch_reason(repo)
    assert reason is not None
    assert f"git exit {code}" in reason


def test_git_not_runnable_is_refused(repo, monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(env_smoke.subprocess, "run", boom)
    reason = _mirror_multi_branch_reason(repo)
    assert reason is not None
    assert "could not read remote.origin.fetch" in reason


def test_repo_without_origin_is_still_not_applicable(repo):
    assert _mirror_multi_branch_reason(repo) is None


def test_main_only_mirror_still_passes(repo):
    _set_main_refspec(repo)
    assert _mirror_multi_branch_reason(repo) is None


def test_for_each_ref_failure_is_refused(repo, monkeypatch):
    _set_main_refspec(repo)
    monkeypatch.setattr(env_smoke.subprocess, "run", _fail("for-each-ref", 128))
    reason = _mirror_multi_branch_reason(repo)
    assert reason is not None
    assert "could not list refs (git exit 128)" in reason


def test_for_each_ref_not_runnable_is_refused(repo, monkeypatch):
    _set_main_refspec(repo)

    def boom(args, **kwargs):
        if "for-each-ref" in args:
            raise OSError("no git")
        return _REAL_RUN(args, **kwargs)

    monkeypatch.setattr(env_smoke.subprocess, "run", boom)
    reason = _mirror_multi_branch_reason(repo)
    assert reason is not None
    assert "could not list refs" in reason
