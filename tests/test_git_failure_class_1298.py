"""Git failures are infrastructure errors; explicit missing commits remain candidate failures."""

import subprocess

import pytest

from cairnwork import deferred_store, deferred_tests, gitprobe
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
    if isinstance(cause, subprocess.CalledProcessError):
        assert commit_present(tmp_path, "a" * 40) is False
    else:
        with pytest.raises(OSError):
            commit_present(tmp_path, "a" * 40)


def test_commit_present_only_exit_one_means_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("cairnwork.gitcmd.run_git", lambda *a, **kw: "", raising=False)
    assert commit_present(tmp_path, "a" * 40) is False


def test_commit_present_reports_a_broken_repository_as_missing(tmp_path):
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
    origin = tmp_path / "origin"
    _commit_repo(origin)
    repo = tmp_path / "repo"
    _commit_repo(repo)
    with pytest.raises(Rejected) as exc:
        deferred_tests.ensure_test_commit(
            repo, "a" * 40, origin_url=str(origin), allowed_protocols=("file",)
        )
    assert exc.value.code == "TEST_COMMIT_UNREADABLE"


def test_ensure_test_commit_origin_timeout_is_infrastructure(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    with pytest.raises(OSError, match="no verifier-owned origin"):
        deferred_tests.ensure_test_commit(repo, "a" * 40)


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
    with pytest.raises(OSError, match="no verifier-owned origin"):
        deferred_tests.ensure_test_commit(repo, "b" * 40)


def test_unreachable_origin_is_infrastructure(tmp_path):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    _git(repo, "remote", "add", "origin", str(tmp_path / "gone"))
    with pytest.raises(OSError, match="reach origin"):
        deferred_tests.ensure_test_commit(
            repo, "b" * 40, origin_url=str(tmp_path / "gone"), allowed_protocols=("file",)
        )


def test_reachable_origin_without_commit_still_rejects_candidate(tmp_path):
    origin = tmp_path / "origin"
    _commit_repo(origin)
    repo = tmp_path / "repo"
    _commit_repo(repo)
    _git(repo, "remote", "add", "origin", str(origin))
    with pytest.raises(Rejected) as exc:
        deferred_tests.ensure_test_commit(
            repo, "b" * 40, origin_url=str(origin), allowed_protocols=("file",)
        )
    assert exc.value.code == "TEST_COMMIT_UNREADABLE"


def test_candidate_controlled_text_cannot_fake_a_disk_failure(tmp_path):
    cause = subprocess.CalledProcessError(128, ["git"])
    for name in ("No space left.bundle", "Disk quota.bundle", "Read-only file system.bundle"):
        exc = GitCommandError(f"git bundle failed: {tmp_path / name}: No space left on device")
        exc.__cause__ = cause
        with pytest.raises(Rejected) as raised:
            deferred_store._raise_git_failure(exc, supplied=True)
        assert raised.value.code == "TEST_COMMIT_UNREADABLE"


def test_bundle_named_like_disk_error_stays_candidate_rejection(tmp_path):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    store_dir = tmp_path / "store"
    store_dir.mkdir(mode=0o700)
    bundle = store_dir / "No space left.bundle"
    bundle.write_bytes(b"not a bundle")
    with pytest.raises(Rejected) as raised:
        DeferredStore(store_dir).import_bundle(repo, bundle, tmp_path / "used")
    assert raised.value.code == "TEST_COMMIT_UNREADABLE"


def test_real_storage_exhaustion_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setattr(deferred_store, "storage_exhausted", lambda *a: True)
    exc = GitCommandError("git failed")
    exc.__cause__ = subprocess.CalledProcessError(128, ["git"])
    with pytest.raises(OSError, match="storage failure"):
        deferred_store._raise_git_failure(exc)


def test_fetch_ok_but_commit_missing_with_disk_words_stays_candidate_rejection(
    tmp_path, monkeypatch
):
    origin = tmp_path / "origin"
    _commit_repo(origin)
    repo = tmp_path / "repo"
    _commit_repo(repo)
    with pytest.raises(Rejected) as raised:
        deferred_tests.ensure_test_commit(
            repo, "a" * 40, origin_url=str(origin), allowed_protocols=("file",)
        )
    assert raised.value.code == "TEST_COMMIT_UNREADABLE"


def test_storage_exhausted_probe_sees_full_and_healthy(tmp_path, monkeypatch):
    assert gitprobe.storage_exhausted(tmp_path) is False
    monkeypatch.setattr(gitprobe.shutil, "disk_usage", lambda p: type("U", (), {"free": 0})())
    assert gitprobe.storage_exhausted(tmp_path) is True


def test_fetch_failure_with_exhausted_storage_is_infrastructure(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    monkeypatch.setattr(
        gitprobe.tempfile, "mkdtemp", lambda **kw: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        deferred_tests.ensure_test_commit(repo, "a" * 40, origin_url="https://example.invalid/x")


def test_full_verifier_temp_volume_overrides_missing_ref(tmp_path, monkeypatch):
    origin, repo = tmp_path / "origin", tmp_path / "repo"
    _commit_repo(origin)
    _commit_repo(repo)
    monkeypatch.setattr(gitprobe.shutil, "disk_usage", lambda _p: type("Usage", (), {"free": 0})())
    with pytest.raises(OSError, match="storage failure"):
        deferred_tests.ensure_test_commit(
            repo, "b" * 40, origin_url=str(origin), allowed_protocols=("file",)
        )


@pytest.mark.parametrize("read_only", [False, True])
def test_missing_origin_commit_is_unreadable_independent_of_repo_mode(tmp_path, read_only):
    origin, repo = tmp_path / "origin", tmp_path / "repo"
    _commit_repo(origin)
    _commit_repo(repo)
    if read_only:
        (repo / ".git").chmod(0o555)
    try:
        with pytest.raises(Rejected) as raised:
            deferred_tests.ensure_test_commit(
                repo, "b" * 40, origin_url=str(origin), allowed_protocols=("file",)
            )
        assert raised.value.code == "TEST_COMMIT_UNREADABLE"
    finally:
        if read_only:
            (repo / ".git").chmod(0o755)


def test_present_remote_commit_write_into_read_only_repo_is_infrastructure(tmp_path):
    origin, repo = tmp_path / "origin", tmp_path / "repo"
    _commit_repo(origin)
    (origin / "f").write_text("remote-only")
    _git(origin, "add", "f")
    _git(origin, "commit", "-qm", "remote")
    sha = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    _commit_repo(repo)
    (repo / ".git").chmod(0o555)
    try:
        with pytest.raises(OSError):
            deferred_tests.ensure_test_commit(
                repo, sha, origin_url=str(origin), allowed_protocols=("file",)
            )
    finally:
        (repo / ".git").chmod(0o755)


@pytest.mark.parametrize("repo_writable", [True, False])
def test_malformed_bundle_stays_candidate_rejection_whatever_the_repo_mount(
    tmp_path, monkeypatch, repo_writable
):
    repo = tmp_path / "repo"
    _commit_repo(repo)
    store_dir = tmp_path / "store"
    store_dir.mkdir(mode=0o700)
    store = DeferredStore(store_dir)
    sha = "c" * 40
    store.bundle(sha).write_bytes(b"not a bundle")

    def probe(*dirs):  # a read-only supplied repo reads as exhausted if it is ever probed
        return repo in dirs and not repo_writable

    monkeypatch.setattr(deferred_store, "storage_exhausted", probe)
    with pytest.raises(Rejected) as raised:
        store.import_bundle(repo, store.bundle(sha), tmp_path / "used")
    assert raised.value.code == "TEST_COMMIT_UNREADABLE"
