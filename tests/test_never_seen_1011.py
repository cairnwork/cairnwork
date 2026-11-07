"""Fail closed when the test commit exposure check cannot read the repository."""

import subprocess

import pytest

import cairnwork.deferred_tests as deferred
from cairnwork.deferred_overlay import TestCommitRejected as Rejected
from cairnwork.gitcmd import run_git


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _repo(path):
    path.mkdir()
    _git(path, "init", "-q")
    _git(
        path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@e",
        "commit",
        "--allow-empty",
        "-qm",
        "base",
    )
    return _git(path, "rev-parse", "HEAD").stdout.decode().strip()


def _unknown(call):
    with pytest.raises(Rejected) as error:
        call()
    assert error.value.code == "TEST_COMMIT_EXPOSURE_UNKNOWN"


def test_absent_commit_is_not_seen(tmp_path):
    _repo(tmp_path / "repo")
    assert deferred.never_seen(tmp_path / "repo", "1" * 40) is None


def test_present_commit_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    sha = _repo(repo)
    with pytest.raises(Rejected) as error:
        deferred.never_seen(repo, sha)
    assert error.value.code == "TEST_COMMIT_ALREADY_EXPOSED"


def test_non_repository_is_unknown(tmp_path):
    _unknown(lambda: deferred.never_seen(tmp_path, "1" * 40))


def test_corrupt_object_store_is_unknown(tmp_path):
    repo = tmp_path / "repo"
    sha = _repo(repo)
    object_path = repo / ".git" / "objects" / sha[:2] / sha[2:]
    object_path.chmod(0o644)
    object_path.write_bytes(b"corrupt")
    _unknown(lambda: deferred.never_seen(repo, sha))


def test_timeout_is_unknown_through_git_timeout_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _repo(repo)

    def timed_out(path, *args, **kwargs):
        return run_git(path, *args, **kwargs, timeout=1e-9)

    monkeypatch.setattr(deferred, "run_git", timed_out)
    _unknown(lambda: deferred.never_seen(repo, "1" * 40))
