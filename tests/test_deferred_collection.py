"""Deferred tests a verifier target names but pytest does not run (#660). The target rule (#651,
``untargeted``) reads which tests a target names; these checks cover what pytest collects:
reading the file (every node id pytest collects a new test under, and DEFERRED_TESTS_NOT_COLLECTED)
and, after the base run, the guard's record (DEFERRED_TESTS_NOT_RUN)."""

from pathlib import Path

import pytest
from conftest import commit_change
from test_deferred_tests import REGRESSION, SeeingWorker, make_test_commit, run
from verify_helpers import FIX, candidate, verify

from cairnwork.core_request import VerifierObservation
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.deferred_tests import (
    base_comparison,
    ensure_test_commit,
    new_tests,
    not_run,
    untargeted,
)
from cairnwork.verifiers import VerifierRun, VerifierSpec

sandbox = pytest.mark.sandbox
K = "tests/test_k.py"
BASE_K = "class TestK:\n    def test_a(self):\n        pass\nclass TestK2(TestK):\n    pass\n"
NEW_K = (
    "from calc import add\nclass TestK:\n    def test_a(self):\n        pass\n"
    "    def test_m(self):\n        assert add(1, 1) == 2\nclass TestK2(TestK):\n    pass\n"
)
INIT = "    def __init__(self):\n        self.n = 1\n"
METHOD = "    def test_m(self):\n        assert add(2, 2) == 4\n"
NOT_COLLECTED = REGRESSION + "class TestK:\n" + INIT + METHOD
NOT_RUN = REGRESSION + "class TestK:\n    __test__ = False\n" + METHOD


def _argv(*targets: str) -> tuple[tuple[str, tuple[str, ...]]]:
    return (("pytest", ("python3", "-m", "pytest", "-q", *targets)),)


def _new_tests(project, files):
    base, test_commit = make_test_commit(project, files)
    ensure_test_commit(project, test_commit)
    return new_tests(project, base, test_commit, tuple(files))


def test_new_tests_hold_every_node_id_pytest_collects_a_new_test_under(project):
    commit_change(project, {K: BASE_K})
    nodes = _new_tests(project, {K: NEW_K})
    assert nodes == {f"{K}::TestK::test_m", f"{K}::TestK2::test_m"}
    assert untargeted((K,), nodes, (f"{K}::TestK::test_m",)) == (K,)
    assert untargeted((K,), nodes, (f"{K}::TestK::test_m", f"{K}::TestK2")) == ()


@pytest.mark.parametrize(
    "layout",
    [
        "class TestK:\n" + INIT + METHOD,
        "class TestK:\n    def __new__(cls):\n        return object.__new__(cls)\n" + METHOD,
        "class Base:\n" + INIT + "class TestK(Base):\n" + METHOD,
    ],
    ids=["layout-1", "layout-2", "layout-3"],
)
def test_a_new_test_pytest_does_not_collect_is_refused(project, layout):
    with pytest.raises(Rejected) as err:
        _new_tests(project, {"tests/test_regression.py": REGRESSION + layout})
    assert err.value.code == "DEFERRED_TESTS_NOT_COLLECTED"
    assert "tests/test_regression.py::TestK::test_m" in str(err.value)


def _base_run(outcomes, rc=1):
    return VerifierRun(
        VerifierSpec("v", ("tests/test_x.py",)), rc, False, (), Path("x"),
        VerifierObservation("v", "x/verifier.log", "sha256:0", False), outcomes, recorded=True,
    )  # fmt: skip


def test_the_base_run_must_record_every_new_test_before_teeth_count():
    hit, lost = "tests/test_x.py::test_hit", "tests/test_x.py::TestK::test_lost"
    got = base_comparison([_base_run({hit: "failed"})], frozenset({hit, lost}))
    assert (got.kind, got.code) == ("NOT_RUN", "DEFERRED_TESTS_NOT_RUN")
    assert lost in got.detail and hit not in got.detail
    ran = _base_run({hit: "failed", f"{lost}[1]": "passed"})  # a parametrisation counts
    assert base_comparison([ran], frozenset({hit, lost})).kind == "TEETH"
    split = [_base_run({hit: "failed"}), _base_run({lost: "passed"}, rc=0)]
    assert base_comparison(split, frozenset({hit, lost})).kind == "TEETH"
    broken = _base_run({hit: "failed"}, rc=3)  # cannot tell: UNCOMPARABLE comes first
    assert base_comparison([broken], frozenset({hit, lost})).kind == "UNCOMPARABLE"


@sandbox
@pytest.mark.parametrize(
    "targets, status",
    [((f"{K}::TestK::test_m",), "FAILED"), ((f"{K}::TestK::test_m", f"{K}::TestK2"), "COMPLETED")],
    ids=["one", "each"],
)
def test_worker_path_needs_a_target_for_each_collected_node_id(project, tmp_path, targets, status):
    commit_change(project, {K: BASE_K})
    base, test_commit = make_test_commit(project, {K: NEW_K})
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv(*targets))  # fmt: skip
    assert result.status == status, result.reason
    if status == "FAILED":
        assert result.reason.startswith(f"DEFERRED_TESTS_NOT_TARGETED: {K};"), result.reason


@sandbox
def test_worker_path_refuses_a_new_test_pytest_does_not_collect(project, tmp_path):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": NOT_COLLECTED})
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv("tests/test_regression.py"))  # fmt: skip
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_COLLECTED"), result.reason
    assert "tests/test_regression.py::TestK::test_m" in result.reason
    assert list((tmp_path / "state" / "runs").glob("*-b-v*")) == []  # refused before a base run


@sandbox
def test_worker_path_refuses_a_new_test_the_base_run_did_not_run(project, tmp_path):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": NOT_RUN})
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv("tests/test_regression.py"))  # fmt: skip
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_RUN"), result.reason
    assert "tests/test_regression.py::TestK::test_m" in result.reason


@sandbox
def test_verify_refuses_a_new_test_the_base_run_did_not_run(project, state):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": NOT_RUN})
    cand = candidate(project, base, {"src/calc.py": FIX})
    command = "python3 -m pytest -q tests/test_regression.py"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert code == 2 and out["status"] == "REFUSED" and out["verified"] is False
    assert out["reason"].startswith("DEFERRED_TESTS_NOT_RUN"), out
    assert "tests/test_regression.py::TestK::test_m" in out["reason"]


# #697: the verification run's record must hold every new test too. TestK is collected on the base
# (add is off by one, so it fails there: teeth) and not on the candidate, where the whole-file
# target still passes on test_regression.
GONE = REGRESSION + "class TestK:\n    __test__ = add(1, 1) != 2\n" + METHOD


def test_not_run_names_a_target_missing_from_the_record_even_beside_its_file():
    other, named = "tests/test_x.py::test_other", "tests/test_x.py::TestK::test_m"
    record = [_base_run({other: "passed"}, rc=0)]  # same file, another test: not enough
    assert not_run(record, frozenset({other, named})) == [named]
    assert not_run([_base_run({other: "passed", f"{named}[1]": "passed"})], {other, named}) == []


@sandbox
def test_worker_path_refuses_a_new_test_the_verification_run_did_not_run(project, tmp_path):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": GONE})
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv("tests/test_regression.py"))  # fmt: skip
    assert result.status == "FAILED" and not result.verified
    assert result.reason.startswith("DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION"), result.reason
    assert "tests/test_regression.py::TestK::test_m" in result.reason
    assert "test_regression.py::test_regression" not in result.reason


@sandbox
def test_worker_path_completes_when_the_verification_run_ran_every_new_test(project, tmp_path):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    result = run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
                 verifiers=_argv("tests/test_regression.py"))  # fmt: skip
    assert result.status == "COMPLETED" and result.verified, result.reason


@sandbox
def test_verify_refuses_a_new_test_the_verification_run_did_not_run(project, state):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": GONE})
    cand = candidate(project, base, {"src/calc.py": FIX})
    command = "python3 -m pytest -q tests/test_regression.py"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(command,))
    assert out["verified"] is False, out
    assert out["reason"].startswith("DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION"), out
    assert "tests/test_regression.py::TestK::test_m" in out["reason"]
