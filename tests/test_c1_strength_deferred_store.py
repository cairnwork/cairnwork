"""Focused contract checks for deferred test storage mutations."""

import subprocess
import tempfile
from pathlib import Path

import pytest
from conftest import git
from test_deferred_store import make_bundle, used_dir
from test_deferred_store import store as store  # noqa: F401

import cairnwork.deferred_store as deferred_store
from cairnwork.deferred_store import DeferredStore, _only_parent, consume
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError

SHA = "a" * 40


def test_consume_records_once_and_check_only_does_not_create_marker(tmp_path):
    used = tmp_path / "verify-used-tests"

    consume(used, SHA, record=False)
    assert not (used / SHA).exists()

    consume(used, SHA, record=True)
    marker = used / SHA
    assert marker.is_file()
    assert marker.stat().st_mode & 0o777 == 0o600
    with pytest.raises(Rejected) as raised:
        consume(used, SHA, record=True)
    assert raised.value.code == "TEST_COMMIT_ALREADY_EXPOSED"


def test_consume_rejects_non_commit_identifiers_before_creating_state(tmp_path):
    used = tmp_path / "verify-used-tests"

    with pytest.raises(Rejected) as raised:
        consume(used, "not-a-commit", record=True)

    assert raised.value.code == "TEST_COMMIT_INVALID"
    assert not used.exists()


def test_require_imported_preserves_missing_commit_identity(tmp_path):
    store_path = tmp_path / "deferred-tests"
    store_path.mkdir(mode=0o700)
    strict = DeferredStore(store_path, required=True)

    with pytest.raises(Rejected) as raised:
        strict.require_imported(SHA)

    assert raised.value.code == "TEST_COMMIT_NOT_IMPORTED"
    assert SHA in str(raised.value)


def test_fetch_into_preserves_missing_commit_identity(tmp_path):
    store_path = tmp_path / "deferred-tests"
    store_path.mkdir(mode=0o700)
    store = DeferredStore(store_path)

    with pytest.raises(Rejected) as raised:
        store.fetch_into(tmp_path / "repo", SHA)

    assert raised.value.code == "TEST_COMMIT_NOT_IMPORTED"
    assert SHA in str(raised.value)


def test_parent_preserves_missing_commit_identity(tmp_path):
    store_path = tmp_path / "deferred-tests"
    store_path.mkdir(mode=0o700)

    with pytest.raises(Rejected) as raised:
        DeferredStore(store_path).parent(SHA, tmp_path / "repo")

    assert raised.value.code == "TEST_COMMIT_NOT_IMPORTED"
    assert SHA in str(raised.value)


def test_has_does_not_follow_bundle_symlinks(tmp_path):
    store_path = tmp_path / "deferred-tests"
    store_path.mkdir(mode=0o700)
    store = DeferredStore(store_path)
    target = tmp_path / "target.bundle"
    target.write_bytes(b"bundle placeholder")
    store.bundle(SHA).symlink_to(target)

    assert store.has(SHA) is False


def test_bundle_rejects_invalid_commit_identifier_with_stable_code(tmp_path):
    store_path = tmp_path / "deferred-tests"
    store_path.mkdir(mode=0o700)

    with pytest.raises(Rejected) as raised:
        DeferredStore(store_path).bundle("invalid")

    assert raised.value.code == "TEST_COMMIT_INVALID"


@pytest.mark.parametrize("parents", ["", f"{SHA}\n", f"{SHA} {SHA} {SHA}\n", f"{SHA} invalid\n"])
def test_only_parent_requires_exactly_one_full_parent(monkeypatch, tmp_path, parents):
    monkeypatch.setattr("cairnwork.deferred_store.run_git", lambda *_a, **_k: parents)

    with pytest.raises(Rejected) as raised:
        _only_parent(tmp_path)

    assert raised.value.code == "TEST_COMMIT_NOT_ON_BASE"


def test_only_parent_returns_the_single_full_parent(monkeypatch, tmp_path):
    commit = "b" * 40
    monkeypatch.setattr("cairnwork.deferred_store.run_git", lambda *_a, **_k: f"{commit} {SHA}\n")

    assert _only_parent(tmp_path) == SHA


def test_check_dir_rejects_a_regular_file(tmp_path):
    store_path = tmp_path / "not-a-directory"
    store_path.write_text("x")
    store_path.chmod(0o600)

    with pytest.raises(Rejected) as raised:
        DeferredStore(store_path).check_dir()

    assert raised.value.code == "TEST_STORE_UNSAFE"


def test_import_scratch_stays_inside_private_store(project, store, tmp_path, monkeypatch):
    _base, _sha, bundle = make_bundle(
        project, {"tests/test_r.py": "def test_r():\n    pass\n"}, tmp_path
    )
    real_run_git = deferred_store.run_git
    checked = False

    def observe_scratch(repo, *args, **kwargs):
        nonlocal checked
        if args[:2] == ("bundle", "list-heads"):
            checked = True
            assert repo.parent == store.path
        if args[:2] == ("init", "--bare"):
            assert "--template=" in args
        return real_run_git(repo, *args, **kwargs)

    real_mkstemp = deferred_store.tempfile.mkstemp

    def observe_import_temp(*args, **kwargs):
        assert kwargs.get("dir") == store.path
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", observe_scratch)
    monkeypatch.setattr(deferred_store.tempfile, "mkstemp", observe_import_temp)
    store.import_bundle(project, bundle, used_dir(store))

    assert checked


def test_internal_scratch_rev_parse_failure_stays_infrastructure_error(tmp_path, monkeypatch):
    store_path = tmp_path / "deferred-tests"
    store_path.mkdir(mode=0o700)
    repo = tmp_path / "not-a-repository"
    repo.mkdir()
    monkeypatch.setattr(DeferredStore, "has", lambda _self, _sha: True)
    real_run_git = deferred_store.run_git

    def fail_rev_parse(path, *args, **kwargs):
        if args == ("rev-parse", "--git-common-dir"):
            cause = subprocess.CalledProcessError(128, ["git", "rev-parse"])
            raise GitCommandError("git rev-parse failed") from cause
        return real_run_git(path, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", fail_rev_parse)
    with pytest.raises(OSError) as raised:
        DeferredStore(store_path).parent(SHA, repo)

    assert not isinstance(raised.value, Rejected)


def test_default_temp_volume_exhaustion_stays_an_infrastructure_error(monkeypatch):
    temp_root = Path(tempfile.gettempdir())
    monkeypatch.setattr(deferred_store, "storage_exhausted", lambda *dirs: temp_root in dirs)
    error = GitCommandError("git failed")
    error.__cause__ = subprocess.CalledProcessError(128, ["git"])

    with pytest.raises(OSError, match="storage failure"):
        deferred_store._raise_git_failure(error)


def test_canonical_bundle_keeps_parent_as_prerequisite(project, store, tmp_path):
    base, sha, bundle = make_bundle(
        project, {"tests/test_r.py": "def test_r():\n    pass\n"}, tmp_path
    )
    store.import_bundle(project, bundle, used_dir(store))
    empty = tmp_path / "empty.git"
    git(tmp_path, "init", "--bare", "-q", str(empty))

    verify = subprocess.run(
        ["git", "-C", str(empty), "bundle", "verify", str(store.bundle(sha))],
        check=False,
        capture_output=True,
        text=True,
    )

    assert verify.returncode != 0
    assert base in verify.stderr
