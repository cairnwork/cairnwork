"""Issue #1250: classify canonical bundle creation failures as environment errors."""
# ruff: noqa: F811

import subprocess

import pytest
from test_deferred_store import make_bundle, store, used_dir  # noqa: F401

import cairnwork.deferred_store as deferred_store
from cairnwork.deferred_tests import TestCommitRejected as CommitRejected
from cairnwork.gitcmd import GitCommandError


def _git_failure(command, cause):
    error = GitCommandError(f"git {command} failed")
    error.__cause__ = cause
    return error


def test_bundle_create_process_failure_is_environment_error(project, store, tmp_path, monkeypatch):  # noqa: F811
    _base, _test_commit, bundle = make_bundle(
        project,
        {"tests/test_regression.py": "def test_regression():\n    assert True\n"},
        tmp_path,
    )
    original_run_git = deferred_store.run_git

    def fail_bundle_create(repo, *args, **kwargs):
        if args[:2] == ("bundle", "create"):
            failure = subprocess.CalledProcessError(128, ["git", *args], stderr="disk")
            raise _git_failure("bundle create", failure)
        return original_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fail_bundle_create)
    with pytest.raises(OSError, match="could not run git") as err:
        store.import_bundle(project, bundle, used_dir(store))

    assert not isinstance(err.value, CommitRejected)


def test_bundle_create_timeout_keeps_candidate_refusal_code(project, store, tmp_path, monkeypatch):  # noqa: F811
    _base, _sha, bundle = make_bundle(
        project, {"tests/test_candidate.py": "def test_candidate():\n    pass\n"}, tmp_path
    )
    original_run_git = deferred_store.run_git

    def timeout_bundle_create(repo, *args, **kwargs):
        if args[:2] == ("bundle", "create"):
            timeout = subprocess.TimeoutExpired(["git", *args], 1)
            raise _git_failure("bundle create", timeout)
        return original_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", timeout_bundle_create)
    with pytest.raises(CommitRejected) as err:
        store.import_bundle(project, bundle, used_dir(store))

    assert err.value.code == "TEST_COMMIT_UNREADABLE"


@pytest.mark.parametrize("command", ["bundle list-heads", "fetch"])
def test_candidate_validation_failures_keep_refusal_code(
    project, store, tmp_path, monkeypatch, command
):  # noqa: F811
    _base, _sha, bundle = make_bundle(
        project, {"tests/test_candidate.py": "def test_candidate():\n    pass\n"}, tmp_path
    )
    original_run_git = deferred_store.run_git

    def fail_candidate_command(repo, *args, **kwargs):
        matches = (
            args[:2] == ("bundle", "list-heads")
            if command == "bundle list-heads"
            else args[:1] == ("fetch",)
        )
        if matches:
            failure = subprocess.CalledProcessError(128, ["git", *args], stderr="invalid")
            raise _git_failure(command, failure)
        return original_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fail_candidate_command)
    with pytest.raises(CommitRejected) as err:
        store.import_bundle(project, bundle, used_dir(store))

    assert err.value.code == "TEST_COMMIT_UNREADABLE"
