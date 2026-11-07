"""Fail closed when the test commit exposure check cannot read the repository."""

import os
import subprocess

import pytest

import cairnwork.deferred_tests as deferred
from cairnwork.deferred_overlay import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError, run_git


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


def test_an_object_store_read_error_is_unknown_not_unseen(tmp_path):
    """Review r1: git also exits 1 when it cannot read the object store, and then says why."""
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(f"{tmp_path / 'gone'}\n")
    _unknown(lambda: deferred.never_seen(repo, "1" * 40))


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_object_directory_is_unknown_not_unseen(tmp_path):
    repo = tmp_path / "repo"
    sha = _repo(repo)
    fan_out = repo / ".git" / "objects" / sha[:2]
    fan_out.chmod(0)
    try:
        _unknown(lambda: deferred.never_seen(repo, sha))
    finally:
        fan_out.chmod(0o755)


def test_plain_run_git_still_raises_on_exit_1(tmp_path):
    """Review r1: the ok_codes default lets no other caller read a failure as success."""
    _repo(tmp_path / "repo")
    with pytest.raises(GitCommandError):
        run_git(tmp_path / "repo", "rev-parse", "--verify", "--quiet", "1" * 40 + "^{commit}")
