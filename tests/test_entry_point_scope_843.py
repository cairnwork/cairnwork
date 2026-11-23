"""#843 gap 2: a test commit that carries a pytest11 entry point (a ``*.dist-info`` or
``*.egg-info`` directory, an ``.egg-link``) is refused with TEST_COMMIT_SCOPE, like a ``.pth``."""

import pytest
from conftest import git

from cairnwork.deferred_tests import TestCommitRejected
from cairnwork.deferred_tests import test_paths as paths_of
from cairnwork.test_commit_paths import out_of_scope

MARK = "ENTRYPOINT_MARK_843"  # a unique string a plugin would print; it must never run


def _commit(repo, *names):
    git(repo, "read-tree", "HEAD")
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    for name in names:
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    tree = git(repo, "write-tree")
    git(repo, "read-tree", "HEAD")
    return git(repo, "commit-tree", tree, "-p", "HEAD", "-m", f"c {MARK}")


BAD = [
    "tests/evil-1.0.dist-info/entry_points.txt",
    "tests/EVIL.DIST-INFO/METADATA",
    "tests/evil.egg-info/entry_points.txt",
    "tests/sub/evil.egg-link",
    "tests/x.pth",
]


@pytest.mark.parametrize("path", BAD)
def test_real_entry_refuses_entry_point_paths(repo, path):
    tc = _commit(repo, "tests/test_ok.py", path)
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, git(repo, "rev-parse", "HEAD"), tc)
    assert err.value.code == "TEST_COMMIT_SCOPE" and path in str(err.value)


def test_plain_test_files_still_pass(repo):
    tc = _commit(repo, "tests/test_ok.py", "tests/data/info.txt", "tests/dist_info_notes.md")
    added, _ = paths_of(repo, git(repo, "rev-parse", "HEAD"), tc)
    assert "tests/test_ok.py" in added


@pytest.mark.parametrize("status", ["A", "M", "D"])
def test_every_status_refused(status):
    assert out_of_scope("tests/e-1.dist-info/entry_points.txt", status)
