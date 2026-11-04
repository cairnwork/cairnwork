"""#798: a test commit may delete test files. overlay-v1 removes each deleted path (it must be
absent from the test commit); ``TestFiles.deleted`` carries them to lay_tests_over."""

import pytest
from conftest import commit_change, git

from cairnwork.deferred_overlay import TestFiles, deleted_of
from cairnwork.deferred_tests import TestCommitRejected, lay_tests_over, overlay_tree


@pytest.fixture
def three(repo):
    base = commit_change(
        repo, {"tests/test_a.py": "def test_a(): pass\n", "tests/test_b.py": "b\n"}
    )
    head = commit_change(repo, {"src/a.py": "x = 2\n"})
    git(repo, "checkout", "-q", "--detach", base)
    tests = commit_change(
        repo, {"tests/test_new.py": "def test_new(): pass\n"}, delete=("tests/test_a.py",)
    )
    git(repo, "checkout", "-q", "-f", "main")
    return base, head, tests


def _names(repo, tree):
    return git(repo, "ls-tree", "-r", "--name-only", tree).splitlines()


def test_a_deleted_test_file_is_gone_from_the_executed_tree(repo, three, tmp_path):
    _base, head, tests = three
    tree = overlay_tree(
        repo, head, tests, ("tests/test_new.py",), tmp_path / "i", ("tests/test_a.py",)
    )
    gone = set(_names(repo, head)) - set(_names(repo, tree))
    assert gone == {"tests/test_a.py"} and "tests/test_new.py" in _names(repo, tree)
    without = overlay_tree(repo, head, tests, ("tests/test_new.py",), tmp_path / "j")
    assert "tests/test_a.py" in _names(repo, without)  # not deleted unless named


@pytest.mark.parametrize(
    ("deleted", "paths"),
    [
        (("tests/test_b.py",), ()),  # still in the test commit: nothing deleted it
        (("tests/test_a.py",), ("tests/test_a.py",)),  # both laid and deleted
        (("tests/../src/a.py",), ()),
        (("tests/.git/x",), ()),
    ],
)
def test_a_deleted_path_the_test_commit_does_not_delete_is_unsupported(
    repo, three, tmp_path, deleted, paths
):
    _base, head, tests = three
    with pytest.raises(TestCommitRejected) as exc:
        overlay_tree(repo, head, tests, paths, tmp_path / "i", deleted)
    assert exc.value.code == "OVERLAY_UNSUPPORTED_ENTRY"


def test_lay_tests_over_deletes_what_the_test_files_carry(repo, three, tmp_path):
    _base, head, tests = three
    files = TestFiles(("tests/test_new.py",), ("tests/test_a.py",))
    (tmp_path / "runs").mkdir()
    judged = lay_tests_over(repo, head, tests, files, "a0123456789abcdef", tmp_path / "runs")
    expected = overlay_tree(repo, head, tests, tuple(files), tmp_path / "i", files.deleted)
    assert git(repo, "rev-parse", f"{judged}^{{tree}}") == expected
    assert "tests/test_a.py" not in _names(repo, judged)


def test_plain_tuples_delete_nothing():
    assert deleted_of(("tests/test_a.py",)) == () and deleted_of(()) == ()
    assert deleted_of(TestFiles(("a",), ("b",))) == ("b",) and TestFiles(("a",)) == ("a",)
