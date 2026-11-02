"""Each deferred test file's changes are run by some verifier (#651): a receipt binds every test
path of the test commit, so the worker path and ``cairnwork-verify`` both require each Python test
file to be a verifier target, whole or through each of its new or changed tests, before the tests
run on the base."""

import pytest
from test_deferred_tests import REGRESSION, SeeingWorker, make_test_commit, run
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_tests import untargeted

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
