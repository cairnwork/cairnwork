"""#495: a base test file that git cannot read is not a new file.

``deferred_tests._tree`` reads the base side of each changed test file with ``git show``.
Only a path that is absent from the base tree means "new file"; a file that exists but
cannot be read (non-UTF-8 content, a git failure, a failed existence check) is refused as
TEST_COMMIT_UNREADABLE instead of silently counting its tests as newly added."""

import pytest
from conftest import git

import cairnwork.deferred_tests as dt


def test_non_utf8_base_file_is_unreadable(repo):
    path = "tests/latin.py"
    (repo / "tests").mkdir(exist_ok=True)
    (repo / path).write_bytes(b"# \xe9\n")
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", "latin")
    with pytest.raises(dt.TestCommitRejected) as err:
        dt._tree(repo, f"HEAD:{path}")
    assert err.value.code == "TEST_COMMIT_UNREADABLE"


def test_missing_base_file_returns_none(repo):
    assert dt._tree(repo, "HEAD:tests/missing.py") is None


def test_base_existence_check_failure_is_unreadable(repo, monkeypatch):
    def fail(_repo, *args):
        raise dt.GitCommandError("git ls-tree failed")

    monkeypatch.setattr(dt, "run_git", fail)
    with pytest.raises(dt.TestCommitRejected) as err:
        dt._tree(repo, "deadbeef:tests/missing.py")
    assert err.value.code == "TEST_COMMIT_UNREADABLE"
