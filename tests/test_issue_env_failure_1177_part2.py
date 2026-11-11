"""Environment failures in Cairnwork-owned git operations leave Issues queued (#1177 part 2)."""
# ruff: noqa: F811

import json
import subprocess

import pytest
from conftest import git
from test_issue_runner import BLOCK, FakeGitHub, issue, setup  # noqa: F401

import cairnwork.deferred_store as deferred_store
import cairnwork.intake as intake_module
import cairnwork.issue_runner as runner
from cairnwork.deferred_store import DeferredStore
from cairnwork.gitcmd import GitCommandError


def _git_error(cause):
    error = GitCommandError("git fetch failed")
    error.__cause__ = cause
    return error


def _assert_queued_error(out, github):
    assert "error" in out and "refused" not in out
    assert github.labels[7] == {runner.QUEUE}
    assert not github.comments


@pytest.mark.parametrize("failure", [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 1)])
def test_deferred_store_environment_failure_stays_queued(setup, monkeypatch, failure):
    test_commit = "c" * 40
    github = FakeGitHub([issue(block={**BLOCK, "test_commit": test_commit})])
    github.commits[test_commit] = [git(setup["clone"], "rev-parse", "HEAD")]
    monkeypatch.setattr(DeferredStore, "has", lambda self, sha: True)
    monkeypatch.setattr(
        deferred_store,
        "run_git",
        lambda *args, **kwargs: (_ for _ in ()).throw(_git_error(failure)),
    )
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    _assert_queued_error(out, github)


def test_deferred_store_actual_git_refusal_still_needs_fix(setup, monkeypatch):
    test_commit = "c" * 40
    github = FakeGitHub([issue(block={**BLOCK, "test_commit": test_commit})])
    monkeypatch.setattr(DeferredStore, "has", lambda self, sha: True)
    monkeypatch.setattr(
        deferred_store,
        "run_git",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            _git_error(subprocess.CalledProcessError(1, "git"))
        ),
    )
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["refused"].startswith("TASK_TEST_COMMIT_UNREADABLE")
    assert github.labels[7] == {runner.NEEDS_FIX}


@pytest.mark.parametrize("failure", [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 1)])
def test_mirror_base_environment_failure_stays_queued(setup, monkeypatch, failure):
    mirror = setup["clone"].parent / "state-mirror"
    git(setup["clone"], "clone", "-q", str(setup["clone"]), str(mirror))
    (setup["intake"].state / "config.json").write_text(json.dumps({"mirror": str(mirror)}))
    github = FakeGitHub([issue()])

    def fail(*args, **kwargs):
        raise _git_error(failure)

    monkeypatch.setattr(intake_module, "run_git", fail)
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    _assert_queued_error(out, github)


def test_mirror_base_actual_git_refusal_still_needs_fix(setup, monkeypatch):
    mirror = setup["clone"].parent / "state-mirror"
    git(setup["clone"], "clone", "-q", str(setup["clone"]), str(mirror))
    (setup["intake"].state / "config.json").write_text(json.dumps({"mirror": str(mirror)}))
    github = FakeGitHub([issue()])

    def refuse(*args, **kwargs):
        raise _git_error(subprocess.CalledProcessError(128, "git"))

    monkeypatch.setattr(intake_module, "run_git", refuse)
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["refused"].startswith("BAD_REQUEST")
    assert github.labels[7] == {runner.NEEDS_FIX}
