"""#798: a test commit may delete (or move) a test file. The deleted file is gone from the judged
commit, Core is told the deletion is allowed (only for that file), and a candidate that does not
fix the bug is still refused. What stays refused: a deleted conftest, a deleted non-test file and
a deleted symlink."""

import os

import pytest
from conftest import commit_change, git
from test_deferred_tests import FIX, REGRESSION, SeeingWorker, make_test_commit, run
from test_deferred_tests import project as _project  # noqa: F401  (the fixture, then extended)

pytestmark = pytest.mark.sandbox

OLD = "from calc import add\n\n\ndef test_old():\n    assert add(1, 1) == 3  # the bug, pinned\n"
MOVED = "from calc import add\n\n\ndef test_old_moved():\n    assert add(1, 1) == 2\n"
NO_FIX = {"src/calc.py": "def add(a, b):\n    return a + b + 1  # still wrong\n"}
BOTH = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_regression.py")),)
MOVE_VERIFIERS = (
    (
        "pytest",
        ("python3", "-m", "pytest", "-q", "tests/test_regression.py", "tests/test_moved.py"),
    ),
)


@pytest.fixture
def project(_project):  # noqa: F811
    commit_change(_project, {"tests/test_old.py": OLD})
    os.symlink("test_old.py", _project / "tests/link.py")
    commit_change(_project, {}, message="a symlink in the tests")
    return _project


def _files(project, base, commit):
    return git(project, "diff", "--name-status", base, commit).splitlines()


def test_a_test_commit_that_deletes_an_old_test_is_verified_and_the_file_is_gone(project, tmp_path):
    base, tests = make_test_commit(project, {"tests/test_regression.py": REGRESSION},
                                   delete=("tests/test_old.py",))  # fmt: skip
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX))
    assert result.status == "COMPLETED", result.reason
    assert sorted(_files(project, base, result.candidate_commit)) == [
        "A\ttests/test_regression.py", "D\ttests/test_old.py", "M\tsrc/calc.py",
    ]  # fmt: skip


def test_a_candidate_that_does_not_fix_the_bug_is_still_refused(project, tmp_path):
    base, tests = make_test_commit(project, {"tests/test_regression.py": REGRESSION},
                                   delete=("tests/test_old.py",))  # fmt: skip
    result = run(project, tmp_path, base, tests, SeeingWorker(NO_FIX))
    assert result.status == "FAILED" and "TEST_COMMIT_SCOPE" not in result.reason


def test_a_moved_test_file_is_a_deletion_plus_an_addition(project, tmp_path):
    files = {"tests/test_moved.py": MOVED, "tests/test_regression.py": REGRESSION}
    base, tests = make_test_commit(project, files, delete=("tests/test_old.py",))
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX), verifiers=MOVE_VERIFIERS)
    assert result.status == "COMPLETED", result.reason
    names = [line.split("\t")[1] for line in _files(project, base, result.candidate_commit)]
    assert "tests/test_moved.py" in names and "tests/test_old.py" in names
    assert git(project, "ls-tree", "--name-only", result.candidate_commit, "tests/").split() == [
        "tests/conftest.py", "tests/link.py", "tests/test_moved.py", "tests/test_regression.py",
    ]  # fmt: skip


class DeletingWorker(SeeingWorker):
    def run(self, workdir, task) -> None:
        super().run(workdir, task)
        (workdir / "tests/test_old.py").unlink()


def test_the_worker_still_cannot_delete_a_file_itself(project, tmp_path):
    base, tests = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    result = run(project, tmp_path, base, tests, DeletingWorker(FIX))
    assert result.status == "FAILED" and any(
        c in result.reason for c in ("OUT_OF_SCOPE", "DIRTY_TREE")
    ), result.reason


@pytest.mark.parametrize(
    "delete",
    ["tests/conftest.py", "src/calc.py", "tests/link.py"],
    ids=["conftest", "outside the test root", "symlink"],
)
def test_deleting_anything_but_a_plain_test_file_is_still_refused(project, tmp_path, delete):
    base, tests = make_test_commit(
        project, {"tests/test_regression.py": REGRESSION}, delete=(delete,)
    )
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX))
    assert "TEST_COMMIT_SCOPE" in result.reason


@pytest.mark.parametrize("mode", ["module-dir", "whole-suite"])
def test_a_deleted_file_is_not_a_p2p_target(project, tmp_path, mode):
    """#816 review: with P2P on, the deleted file must not be run on the judged tree (rc 4)."""
    base, tests = make_test_commit(project, {"tests/test_regression.py": REGRESSION},
                                   delete=("tests/test_old.py",))  # fmt: skip
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX), p2p_mode=mode)
    assert result.status == "COMPLETED", result.reason


@pytest.mark.parametrize("mode", ["module-dir", "whole-suite"])
def test_a_moved_file_is_not_a_p2p_target_either(project, tmp_path, mode):
    files = {"tests/test_moved.py": MOVED, "tests/test_regression.py": REGRESSION}
    base, tests = make_test_commit(project, files, delete=("tests/test_old.py",))
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX), verifiers=MOVE_VERIFIERS,
                 p2p_mode=mode)  # fmt: skip
    assert result.status == "COMPLETED", result.reason
