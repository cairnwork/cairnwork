"""#1185: a git failure on Cairnwork's own storage is an environment error; one caused by the
supplied repository keeps its refusal (TEST_COMMIT_UNREADABLE). Review counterexamples."""

import shutil

import pytest
from test_deferred_store import make_bundle, used_dir
from test_deferred_store import project as project  # noqa: F401 - fixture
from test_deferred_store import store as store  # noqa: F401 - fixture

import cairnwork.deferred_store as ds
from cairnwork.deferred_store import DeferredStore
from cairnwork.deferred_tests import TestCommitRejected, ensure_test_commit


def _stored(project, store, tmp_path):  # noqa: F811
    _b, sha, bundle = make_bundle(
        project, {"tests/test_r.py": "def test_r():\n    pass\n"}, tmp_path
    )
    store.import_bundle(project, bundle, used_dir(store))
    return sha


def _break_repo(repo, how):
    git = repo / ".git"
    if how == "fetch_head_dir":
        (git / "FETCH_HEAD").unlink(missing_ok=True)
        (git / "FETCH_HEAD").mkdir()
    elif how == "garbage_head":
        (git / "HEAD").write_text("garbage\n")
    elif how == "no_objects":
        shutil.rmtree(git / "objects")
    elif how == "not_git":
        shutil.rmtree(git)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("how", ["fetch_head_dir", "garbage_head", "no_objects", "not_git"])
def test_a_broken_supplied_repo_is_still_refused(project, store, tmp_path, how, required):  # noqa: F811
    sha = _stored(project, store, tmp_path)
    s = DeferredStore(store.path, required=required)
    _break_repo(project, how)
    with pytest.raises(TestCommitRejected, match="TEST_COMMIT_UNREADABLE"):
        ensure_test_commit(project, sha, s)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("damage", ["corrupt", "truncated"])
def test_a_damaged_stored_bundle_is_an_environment_error(
    project, store, tmp_path, damage, required
):  # noqa: F811
    sha = _stored(project, store, tmp_path)
    s = DeferredStore(store.path, required=required)
    data = s.bundle(sha).read_bytes()
    s.bundle(sha).write_bytes(b"corrupt" if damage == "corrupt" else data[:40])
    with pytest.raises(OSError, match="could not run git"):
        ensure_test_commit(project, sha, s)
    with pytest.raises(OSError, match="could not run git"):
        s.parent(sha, project)


@pytest.mark.parametrize("site", ["rev-list", "init"])
def test_an_injected_store_git_failure_is_an_environment_error(
    project, store, tmp_path, monkeypatch, site
):  # noqa: F811
    import subprocess

    sha = _stored(project, store, tmp_path)
    real = ds.run_git

    def fail(repo, *args, **kw):
        if args and args[0] == site:
            cause = subprocess.CalledProcessError(128, ["git", site], stderr="disk full")
            raise ds.GitCommandError(f"git {site} failed") from cause
        return real(repo, *args, **kw)

    monkeypatch.setattr(ds, "run_git", fail)
    with pytest.raises(OSError, match="could not run git"):
        store.parent(sha, project)
