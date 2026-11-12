"""Issue #1187: supplied-bundle git timeouts and decode errors refuse as TestCommitRejected."""

import subprocess

import pytest
from test_deferred_store import make_bundle, used_dir
from test_deferred_store import store as store  # noqa: F401 - fixture
from test_deferred_tests import REGRESSION
from test_deferred_tests import project as project  # noqa: F401 - fixture

from cairnwork import deferred_store
from cairnwork.deferred_tests import TestCommitRejected
from cairnwork.gitcmd import GitCommandError


def test_supplied_bundle_list_heads_timeout_is_rejected(project, store, tmp_path, monkeypatch):
    _base, _sha, bundle = make_bundle(project, {"tests/test_regression.py": REGRESSION}, tmp_path)
    real_run_git = deferred_store.run_git

    def fake_run_git(repo, *args, **kwargs):
        if args and args[0] == "bundle" and len(args) > 1 and args[1] == "list-heads":
            cause = subprocess.TimeoutExpired(cmd=["git", "bundle", "list-heads"], timeout=60)
            raise GitCommandError("git bundle list-heads failed: timeout") from cause
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fake_run_git)
    with pytest.raises(TestCommitRejected) as err:
        store.import_bundle(project, bundle, used_dir(store))
    assert err.value.code == "TEST_COMMIT_UNREADABLE"


def test_supplied_bundle_fetch_timeout_is_rejected(project, store, tmp_path, monkeypatch):
    _base, _sha, bundle = make_bundle(project, {"tests/test_regression.py": REGRESSION}, tmp_path)
    real_run_git = deferred_store.run_git

    def fake_run_git(repo, *args, **kwargs):
        if args and args[0] == "fetch" and str(bundle) in args:
            cause = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=60)
            raise GitCommandError("git fetch failed: timeout") from cause
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fake_run_git)
    with pytest.raises(TestCommitRejected) as err:
        store.import_bundle(project, bundle, used_dir(store))
    assert err.value.code == "TEST_COMMIT_UNREADABLE"


def test_supplied_bundle_decode_error_is_rejected(project, store, tmp_path, monkeypatch):
    _base, _sha, bundle = make_bundle(project, {"tests/test_regression.py": REGRESSION}, tmp_path)
    real_run_git = deferred_store.run_git

    def fake_run_git(repo, *args, **kwargs):
        if args and args[0] == "bundle" and len(args) > 1 and args[1] == "list-heads":
            cause = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            raise GitCommandError("git bundle list-heads failed: decode error") from cause
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fake_run_git)
    with pytest.raises(TestCommitRejected) as err:
        store.import_bundle(project, bundle, used_dir(store))
    assert err.value.code == "TEST_COMMIT_UNREADABLE"


def test_git_launch_failure_remains_oserror(project, store, tmp_path, monkeypatch):
    _base, _sha, bundle = make_bundle(project, {"tests/test_regression.py": REGRESSION}, tmp_path)
    real_run_git = deferred_store.run_git

    def fake_run_git(repo, *args, **kwargs):
        if args and args[0] == "bundle" and len(args) > 1 and args[1] == "list-heads":
            cause = FileNotFoundError(2, "No such file or directory: 'git'")
            raise GitCommandError("git bundle list-heads failed: not found") from cause
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fake_run_git)
    with pytest.raises(OSError) as err:
        store.import_bundle(project, bundle, used_dir(store))
    assert not isinstance(err.value, TestCommitRejected)
    assert isinstance(err.value.__cause__, GitCommandError)
    assert isinstance(err.value.__cause__.__cause__, FileNotFoundError)


def test_host_mirror_timeout_remains_oserror(project, store, tmp_path, monkeypatch):
    _base, _sha, bundle = make_bundle(project, {"tests/test_regression.py": REGRESSION}, tmp_path)
    real_run_git = deferred_store.run_git

    def fake_run_git(repo, *args, **kwargs):
        if repo == project and args and args[0] == "cat-file" and "-e" in args:
            cause = subprocess.TimeoutExpired(cmd=["git", "cat-file"], timeout=60)
            raise GitCommandError("git cat-file failed: timeout") from cause
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fake_run_git)
    with pytest.raises(OSError) as err:
        store.import_bundle(project, bundle, used_dir(store))
    assert not isinstance(err.value, TestCommitRejected)
    assert isinstance(err.value.__cause__, GitCommandError)
    assert isinstance(err.value.__cause__.__cause__, subprocess.TimeoutExpired)
    assert "Cairnwork could not run git" in str(err.value)


@pytest.mark.parametrize("cause", [None, RuntimeError("unexpected failure")])
def test_supplied_bundle_other_causes_remain_oserror(project, store, tmp_path, monkeypatch, cause):
    _base, _sha, bundle = make_bundle(project, {"tests/test_regression.py": REGRESSION}, tmp_path)
    real_run_git = deferred_store.run_git

    def fake_run_git(repo, *args, **kwargs):
        if args and args[0] == "bundle" and len(args) > 1 and args[1] == "list-heads":
            error = GitCommandError("git bundle list-heads failed")
            if cause is None:
                raise error
            raise error from cause
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fake_run_git)
    with pytest.raises(OSError, match="Cairnwork could not run git") as err:
        store.import_bundle(project, bundle, used_dir(store))
    assert not isinstance(err.value, TestCommitRejected)
