"""Issue #1250: scratch repository failures are environment errors."""

import subprocess

import pytest
from test_deferred_store import make_bundle, used_dir
from test_issue_runner import BLOCK, FakeGitHub, issue

import cairnwork.deferred_store as deferred_store
import cairnwork.issue_runner as runner
from cairnwork.deferred_store import DeferredStore
from cairnwork.deferred_tests import TestCommitRejected as CommitRejected
from cairnwork.gitcmd import GitCommandError

pytest_plugins = ("test_deferred_store", "test_issue_runner")


def _git_failure(command):
    cause = subprocess.CalledProcessError(128, ["git", command], stderr="git failed")
    error = GitCommandError(f"git {command} failed")
    error.__cause__ = cause
    return error


def test_scratch_rev_parse_failure_leaves_issue_queued(setup, monkeypatch):
    test_commit = "c" * 40
    github = FakeGitHub([issue(block={**BLOCK, "test_commit": test_commit})])
    monkeypatch.setattr(DeferredStore, "has", lambda self, sha: True)
    real_run_git = deferred_store.run_git

    def fail_scratch_lookup(repo, *args, **kwargs):
        if args == ("rev-parse", "--git-common-dir"):
            raise _git_failure("rev-parse")
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fail_scratch_lookup)
    result = runner.take(setup["intake"], github, setup["clone"])[0]

    assert "error" in result and "refused" not in result
    assert github.labels[7] == {runner.QUEUE}
    assert github.comments == []


@pytest.mark.parametrize("command", ["bundle list-heads", "fetch"])
def test_candidate_bundle_git_refusal_keeps_its_refusal_code(
    project, store, tmp_path, monkeypatch, command
):  # noqa: F811
    _base, _sha, bundle = make_bundle(
        project, {"tests/test_candidate.py": "def test_candidate():\n    pass\n"}, tmp_path
    )
    real_run_git = deferred_store.run_git

    def fail_supplied_bundle(repo, *args, **kwargs):
        matches = (
            args[:2] == ("bundle", "list-heads")
            if command == "bundle list-heads"
            else args[:1] == ("fetch",)
        )
        if matches:
            raise _git_failure(command)
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fail_supplied_bundle)
    with pytest.raises(CommitRejected) as err:
        store.import_bundle(project, bundle, used_dir(store))

    assert err.value.code == "TEST_COMMIT_UNREADABLE"


@pytest.mark.parametrize("path", ["fetch_into", "import_bundle"])
@pytest.mark.parametrize(
    "cause",
    [
        subprocess.TimeoutExpired("git", 1),
        FileNotFoundError("git"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=["timeout", "git-missing", "undecodable"],
)
def test_scratch_rev_parse_host_failure_on_supplied_repo_is_environment(
    project, store, tmp_path, monkeypatch, path, cause
):  # noqa: F811
    _base, sha, bundle = make_bundle(
        project, {"tests/test_candidate.py": "def test_candidate():\n    pass\n"}, tmp_path
    )
    store.import_bundle(project, bundle, used_dir(store))
    real_run_git = deferred_store.run_git

    def fail_rev_parse(repo, *args, **kwargs):
        if args == ("rev-parse", "--git-common-dir"):
            error = GitCommandError("git rev-parse failed")
            error.__cause__ = cause
            raise error
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fail_rev_parse)
    with pytest.raises(OSError) as err:
        if path == "fetch_into":
            store.fetch_into(project, sha)
        else:
            store.import_bundle(project, bundle, used_dir(store))

    assert not isinstance(err.value, CommitRejected)
