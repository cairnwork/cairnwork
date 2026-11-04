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


def test_a_file_replaced_by_a_directory_deletes_before_it_adds(repo, tmp_path):
    """#816 review: deleting tests/helper and adding tests/helper/test_a.py is one test commit."""
    base = commit_change(repo, {"tests/helper": "x\n"})
    git(repo, "checkout", "-q", "--detach", base)
    git(repo, "rm", "-q", "tests/helper")
    tests = commit_change(repo, {"tests/helper/test_a.py": "def test_a(): pass\n"})
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(
        repo, base, tests, ("tests/helper/test_a.py",), tmp_path / "i", ("tests/helper",)
    )
    assert "tests/helper/test_a.py" in _names(repo, tree) and "tests/helper" not in _names(
        repo, tree
    )


def test_stray_deletions_are_the_judged_deletes_the_test_commit_did_not_make(repo, three):
    from cairnwork.deferred_overlay import stray_deletions

    base, _head, _tests = three
    both = commit_change(repo, {}, delete=("tests/test_a.py", "tests/test_b.py"), message="x")
    assert stray_deletions(repo, base, both, ("tests/test_a.py",)) == ["tests/test_b.py"]
    assert stray_deletions(repo, base, both, ("tests/test_a.py", "tests/test_b.py")) == []


class _Stop(Exception):
    pass


def _judge_deleting(repo, three, monkeypatch, deleted_by_candidate):
    """judge_candidate up to its stray-deletion check: the two gates that would stop a worker
    deleting files before it (the verifier run and the not-run check) are stubbed away, and the
    step after the check raises _Stop."""
    from types import SimpleNamespace
    from typing import Any

    from cairnwork import pipeline_judge

    base, _, _ = three
    git(repo, "checkout", "-q", "--detach", base)
    judged = commit_change(repo, {"src/a.py": "x = 3\n"}, delete=deleted_by_candidate)
    git(repo, "checkout", "-q", "-f", "main")
    monkeypatch.setattr(pipeline_judge, "busy_once_more", lambda *a, **k: ())
    monkeypatch.setattr(pipeline_judge, "_not_run_here", lambda *a, **k: [])

    def stop(*a, **k):
        raise _Stop

    monkeypatch.setattr(pipeline_judge, "_contract", stop)
    task = SimpleNamespace(blackbox=None, repo=repo, base_commit=base, allow_deletions=False)
    result = SimpleNamespace(candidate_commit=judged, task_id="t", attempt_id="a", logs=[])
    files = TestFiles(("tests/test_new.py",), ("tests/test_a.py",))
    args: tuple[Any, ...] = (task, (), "d", None, result, files, None, lambda phase: None)
    return pipeline_judge.judge_candidate, args, result


def test_judge_candidate_stops_on_a_delete_the_test_commit_did_not_make(repo, three, monkeypatch):
    judge, args, result = _judge_deleting(
        repo, three, monkeypatch, ("tests/test_a.py", "tests/test_b.py")
    )
    judge(*args)  # returns before Core is asked: _contract would raise _Stop
    assert result.reason == "DELETION_OUTSIDE_TEST_COMMIT: ['tests/test_b.py']"


def test_judge_candidate_goes_on_when_it_deletes_only_what_the_test_commit_does(
    repo, three, monkeypatch
):
    judge, args, _ = _judge_deleting(repo, three, monkeypatch, ("tests/test_a.py",))
    with pytest.raises(_Stop):
        judge(*args)
