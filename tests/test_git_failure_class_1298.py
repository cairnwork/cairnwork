"""Git failures are infrastructure errors; explicit missing commits remain candidate failures."""

import subprocess

import pytest

from cairnwork import deferred_store, deferred_tests
from cairnwork.deferred_store import DeferredStore
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError, commit_present


def _git_error(cause):
    error = GitCommandError("git rev-parse failed")
    if cause is not None:
        error.__cause__ = cause
    return error


def _as_oserror(fail):
    try:
        fail()
    except GitCommandError as exc:
        raise OSError("Cairnwork could not run git") from exc


@pytest.mark.parametrize(
    "cause",
    [
        subprocess.CalledProcessError(128, ["git"]),
        subprocess.TimeoutExpired(["git"], 60),
        OSError("git unavailable"),
    ],
)
def test_commit_present_propagates_git_infrastructure_failure(tmp_path, monkeypatch, cause):
    def fail(*args, **kwargs):
        raise _git_error(cause)

    monkeypatch.setattr("cairnwork.gitcmd.run_git", fail)
    with pytest.raises(OSError):
        commit_present(tmp_path, "a" * 40)


def test_commit_present_only_exit_one_means_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("cairnwork.gitcmd.run_git", lambda *a, **kw: "", raising=False)
    assert commit_present(tmp_path, "a" * 40) is False


@pytest.mark.parametrize(
    "cause",
    [
        subprocess.CalledProcessError(128, ["git"]),
        subprocess.TimeoutExpired(["git"], 60),
        OSError("git unavailable"),
    ],
)
def test_reject_known_fails_closed_on_git_infrastructure(tmp_path, monkeypatch, cause):
    store = DeferredStore(tmp_path / "store")

    def fail(*args, **kwargs):
        raise _git_error(cause)

    monkeypatch.setattr(deferred_store, "commit_present", lambda *a: _as_oserror(fail))
    with pytest.raises(OSError):
        store._reject_known(tmp_path, "a" * 40, tmp_path / "used")


def test_reject_known_still_rejects_commit_that_really_exists(tmp_path, monkeypatch):
    store = DeferredStore(tmp_path / "store")
    monkeypatch.setattr(deferred_store, "commit_present", lambda *a: True)
    with pytest.raises(Rejected) as exc:
        store._reject_known(tmp_path, "a" * 40, tmp_path / "used")
    assert exc.value.code == "TEST_COMMIT_ALREADY_EXPOSED"


@pytest.mark.parametrize(
    "cause",
    [
        subprocess.CalledProcessError(128, ["git"]),
        subprocess.TimeoutExpired(["git"], 60),
        OSError("git unavailable"),
    ],
)
def test_ensure_test_commit_does_not_treat_git_failure_as_missing(tmp_path, monkeypatch, cause):
    def fail(*args, **kwargs):
        raise _git_error(cause)

    monkeypatch.setattr(deferred_tests, "commit_present", lambda *a: _as_oserror(fail))
    with pytest.raises(OSError):
        deferred_tests.ensure_test_commit(tmp_path, "a" * 40)


def test_ensure_test_commit_keeps_real_missing_candidate_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(deferred_tests, "origin_reachable", lambda *a: None)
    monkeypatch.setattr(deferred_tests, "commit_present", lambda *a: False)

    def reject_fetch(*args, **kwargs):
        error = GitCommandError("origin rejected commit")
        error.__cause__ = subprocess.CalledProcessError(128, ["git", "fetch"])
        raise error

    monkeypatch.setattr(deferred_tests, "run_git", reject_fetch)
    with pytest.raises(Rejected) as exc:
        deferred_tests.ensure_test_commit(tmp_path, "a" * 40)
    assert exc.value.code == "TEST_COMMIT_UNREADABLE"


def test_ensure_test_commit_origin_timeout_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setattr(deferred_tests, "origin_reachable", lambda *a: None)
    monkeypatch.setattr(deferred_tests, "commit_present", lambda *a: False)

    def timeout(*args, **kwargs):
        error = GitCommandError("git fetch failed: timeout")
        error.__cause__ = subprocess.TimeoutExpired(["git", "fetch"], 300)
        raise error

    monkeypatch.setattr(deferred_tests, "run_git", timeout)
    with pytest.raises(OSError, match="could not run git"):
        deferred_tests.ensure_test_commit(tmp_path, "a" * 40)


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True,
    )  # fmt: skip


def _commit_repo(path):
    path.mkdir()
    _git(path, "init", "-q")
    (path / "f").write_text("x")
    _git(path, "add", "f")
    _git(path, "commit", "-qm", "c")


def test_no_origin_remote_is_infrastructure_not_candidate_rejection(tmp_path):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    with pytest.raises(OSError, match="reach origin"):
        deferred_tests.ensure_test_commit(repo, "b" * 40)


def test_unreachable_origin_is_infrastructure(tmp_path):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    _git(repo, "remote", "add", "origin", str(tmp_path / "gone"))
    with pytest.raises(OSError, match="reach origin"):
        deferred_tests.ensure_test_commit(repo, "b" * 40)


def test_reachable_origin_without_commit_still_rejects_candidate(tmp_path):
    origin = tmp_path / "origin"
    _commit_repo(origin)
    repo = tmp_path / "repo"
    _commit_repo(repo)
    _git(repo, "remote", "add", "origin", str(origin))
    with pytest.raises(Rejected) as exc:
        deferred_tests.ensure_test_commit(repo, "b" * 40)
    assert exc.value.code == "TEST_COMMIT_UNREADABLE"
