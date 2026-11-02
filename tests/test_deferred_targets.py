"""Each deferred test file's changes are run by some verifier (#651): a receipt binds every test
path of the test commit, so the worker path and ``cairnwork-verify`` both require each Python test
file to be a verifier target, whole or through each of its new or changed tests, before the tests
run on the base."""

import pytest
from test_deferred_tests import REGRESSION, SeeingWorker, make_test_commit, run
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.deferred_tests import ensure_test_commit, untargeted
from cairnwork.deferred_tests import test_paths as paths_of_commit

sandbox = pytest.mark.sandbox
TWO_FILES = {
    "tests/test_regression.py": REGRESSION,
    "tests/test_extra.py": "def test_extra():\n    pass\n",
}
ONE = "python3 -m pytest -q tests/test_regression.py"
BOTH = ONE + " tests/test_extra.py"


def _argv(command: str) -> tuple[tuple[str, tuple[str, ...]]]:
    return (("pytest", tuple(command.split())),)


def test_a_file_is_covered_whole_or_by_each_of_its_changed_tests():
    paths = ("tests/data/cases.json", "tests/test_a.py", "tests/test_b.py", "tests/helpers.py")
    nodes = {"tests/test_a.py::test_x", "tests/test_b.py::TestK::test_m", "tests/test_b.py::test_y"}
    whole = ("tests/test_a.py", "tests/test_b.py", "tests/helpers.py")
    assert untargeted(paths, nodes, whole) == ()
    by_node = ("tests/test_a.py::test_x", "tests/test_b.py::TestK", "tests/test_b.py::test_y")
    assert untargeted(paths, nodes, by_node) == ("tests/helpers.py",)  # no test in it: whole only
    assert untargeted(paths, nodes, whole[:1]) == ("tests/test_b.py", "tests/helpers.py")
    partial = ("tests/test_a.py::test_x[1]", "tests/test_b.py::test_y", *whole[2:])
    assert untargeted(paths, nodes, partial) == ("tests/test_a.py", "tests/test_b.py")


def _paths(project, files):
    base, test_commit = make_test_commit(project, files)
    ensure_test_commit(project, test_commit)
    return paths_of_commit(project, base, test_commit)


def _long(size: int) -> str:
    head = "tests/" + ("d" * 100 + "/") * 4  # every part a target name, so only the length counts
    return head + "test_" + "x" * (size - len(head) - 8) + ".py"


def test_a_test_path_over_cores_text_limit_is_refused_up_front(project):
    """#656: receipts and the acceptance spec carry each test path; Core refuses one over 512
    UTF-8 bytes, so the test commit is refused before anything reads it, saying why."""
    assert _paths(project, {_long(512): REGRESSION}) == (_long(512),)
    with pytest.raises(Rejected) as err:
        _paths(project, {_long(513): REGRESSION})
    assert err.value.code == "TEST_COMMIT_SCOPE" and "over 512 UTF-8 bytes" in str(err.value)


@pytest.mark.parametrize(
    "path",
    ["tests/test_a.b.py", "tests/test a.py", "tests/t\u00e9st_a.py", "tests/sub.d/test_a.py"],
)
def test_a_python_test_file_no_verifier_target_can_name_is_refused_up_front(project, path):
    """#656: such a file could only ever end in DEFERRED_TESTS_NOT_TARGETED, which does not say
    why. Data files need no target, so an odd name stays allowed for them."""
    files = {"tests/test_regression.py": REGRESSION, "tests/data.d/cases v2.json": "{}\n"}
    assert set(_paths(project, files)) == set(files)
    with pytest.raises(Rejected) as err:
        _paths(project, {**files, path: REGRESSION})
    assert err.value.code == "TEST_COMMIT_SCOPE" and "no verifier target can name" in str(err.value)


@sandbox
def test_worker_path_runs_deferred_tests_only_when_each_file_is_a_verifier_target(
    project, tmp_path
):
    base, test_commit = make_test_commit(project, TWO_FILES)
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv(ONE))  # fmt: skip
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason
    assert "tests/test_extra.py" in result.reason
    assert list((tmp_path / "state" / "runs").glob("*-b-v*")) == []  # refused before a base run


@sandbox
def test_worker_path_completes_when_every_deferred_test_file_is_targeted(project, tmp_path):
    base, test_commit = make_test_commit(project, TWO_FILES)
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv(BOTH))  # fmt: skip
    assert result.status == "COMPLETED", result.reason


@sandbox
@pytest.mark.parametrize(
    "node, status",
    [("test_add_one", "COMPLETED"), ("test_add", "FAILED")],
    ids=["changed", "other"],
)
def test_worker_path_accepts_a_node_target_for_each_changed_test(project, tmp_path, node, status):
    added = "def test_add_one():\n    assert add(1, 1) == 2\n"
    tests = {
        "tests/test_calc.py": "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
        + added
    }
    base, test_commit = make_test_commit(project, tests)
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv(f"python3 -m pytest -q tests/test_calc.py::{node}"))  # fmt: skip
    assert result.status == status, result.reason
    assert (status == "FAILED") == result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED")


@sandbox
def test_verify_refuses_deferred_tests_until_each_file_is_a_verifier_target(project, state):
    base, test_commit = make_test_commit(project, TWO_FILES)
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(ONE,))
    assert code == 2 and out["status"] == "REFUSED" and out["verified"] is False
    assert out["reason"].startswith("DEFERRED_TESTS_NOT_TARGETED"), out


@sandbox
def test_verify_verifies_when_every_deferred_test_file_is_targeted(project, state):
    base, test_commit = make_test_commit(project, TWO_FILES)
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(BOTH,))
    assert code == 0 and out["status"] == "VERIFIED", out
