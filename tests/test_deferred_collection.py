"""Deferred tests a verifier target names but pytest does not run (#660). The target rule (#651,
``untargeted``) reads which tests a target names; these checks cover what pytest collects:
reading the file (every node id pytest collects a new test under, and DEFERRED_TESTS_NOT_COLLECTED)
and, after the base run, the guard's record (DEFERRED_TESTS_NOT_RUN)."""

import ast
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from conftest import commit_change
from test_deferred_tests import REGRESSION, SeeingWorker, make_test_commit, run
from verify_helpers import FIX, candidate, verify

from cairnwork import deferred_nodes
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
    assert result.reason.startswith("DEFERRED_TESTS_NOT_RUN:"), result.reason
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


class _ToCore(Exception):
    """Raised by the fake notary: the attempt reached Core."""


def _judge(monkeypatch, verifier_run, p2p_runs=(), checks=()):
    from types import SimpleNamespace

    from cairnwork import pipeline_judge
    from cairnwork.verify_context import VerifyContext

    named = "tests/test_x.py::TestK::test_m"
    monkeypatch.setattr(pipeline_judge, "run_verifiers", lambda *a, **k: [verifier_run])
    monkeypatch.setattr(pipeline_judge, "run_plan", lambda *a, **k: (list(p2p_runs), {}))
    monkeypatch.setattr(pipeline_judge, "new_tests", lambda *a, **k: frozenset({named}))
    if checks:  # a declared blackbox whose judged checks are the given verdicts
        monkeypatch.setattr(pipeline_judge, "judge_blackbox", lambda *a, **k: (tuple(checks), None))
    declared = SimpleNamespace(checks=tuple(SimpleNamespace(verifier_id="bb") for _ in checks))
    task = SimpleNamespace(
        blackbox=declared if checks else None, repo=Path("r"), base_commit="b" * 40,
        import_paths=(), allowed_files=(), allow_deletions=False,
        require_protection="", test_commit="c" * 40,
    )  # fmt: skip
    result = SimpleNamespace(candidate_commit="j", task_id="t", attempt_id="a", logs=[])
    notary = SimpleNamespace(notarize=lambda *a: (_ for _ in ()).throw(_ToCore()))
    plan: Any = object()  # run_plan is replaced above, so any stand-in will do
    pipeline_judge.judge_candidate(
        task, (), "d", notary, result, ("tests/test_x.py",), VerifyContext.of(task, Path("x"), "a"),
        lambda phase: None, p2p=plan if p2p_runs else None,
    )  # fmt: skip
    return result, named


def _passed_run(outcomes):
    return VerifierRun(
        VerifierSpec("v", ("tests/test_x.py",)), 0, False, (), Path("x"),
        VerifierObservation("v", "x/verifier.log", "sha256:0", True), outcomes, recorded=True,
    )  # fmt: skip


def test_a_p2p_run_does_not_stand_in_for_the_verification_run(monkeypatch):
    other = "tests/test_x.py::test_other"
    p2p_run = _passed_run({"tests/test_x.py::TestK::test_m": "passed"})
    result, named = _judge(monkeypatch, _passed_run({other: "passed"}), (p2p_run,))
    assert result.reason.startswith("DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION"), result.reason
    assert named in result.reason


def test_a_failed_verification_run_keeps_the_verdict_path(monkeypatch):
    failed = _base_run({"tests/test_x.py::test_other": "failed"})  # observation not passed
    with pytest.raises(_ToCore):  # Core judges the failure; the new code is not used
        _judge(monkeypatch, failed)


def test_a_failed_blackbox_check_keeps_the_verdict_path(monkeypatch):
    from cairnwork.blackbox_judge import CheckVerdict

    observation = VerifierObservation("bb", "x/bb.log", "sha256:0", False)  # the check failed
    verdict = CheckVerdict(observation, "blackbox:c1: MISMATCH: x")
    ran = _passed_run({"tests/test_x.py::test_other": "passed"})  # every pytest run passed
    with pytest.raises(_ToCore):  # Core judges the failed check; the new code is not used
        _judge(monkeypatch, ran, checks=(verdict,))


UT = "import unittest\nfrom django.test import TestCase\n"
HELD = "from calc import add\n"
M = "    def test_m(self):\n        assert add(1, 1) == 2\n"
FILES = {
    "tests/test_u.py": UT + HELD + "class FooTests(TestCase):\n" + M
    + "class BarTest(unittest.TestCase):\n" + M,
}  # fmt: skip


def _nodes(src):
    funcs, bad = deferred_nodes.test_functions(ast.parse(src))
    return set(funcs), set(bad)


def test_unittest_case_subclasses_not_named_test_are_read():
    funcs, bad = _nodes(FILES["tests/test_u.py"])
    assert funcs == {"FooTests::test_m", "BarTest::test_m"} and not bad


def test_a_class_inheriting_an_earlier_case_subclass_is_read():
    src = (
        UT
        + "class Base(TestCase):\n    def test_b(self): pass\n"
        + "class Sub(Base):\n    def test_s(self): pass\n"
    )
    funcs, _ = _nodes(src)
    assert funcs == {"Base::test_b", "Sub::test_b", "Sub::test_s"}


DIAMOND = (
    "class TestBase:\n    def test_case(self): assert 1\n"
    "class TestA(TestBase):\n    pass\n"
    "class TestB(TestBase):\n    def test_case(self): assert {}\n"
    "class TestChild(TestA, TestB):\n    pass\n"
)


@pytest.mark.parametrize("bases", ["TestA, TestB", "TestB, TestA"])
def test_a_change_to_the_method_the_mro_runs_changes_the_inherited_test(bases):
    """#1009: TestChild runs TestB.test_case (C3 MRO), whichever base comes first."""
    src = DIAMOND.replace("TestChild(TestA, TestB)", f"TestChild({bases})")
    funcs = [deferred_nodes.test_functions(ast.parse(src.format(v)))[0] for v in (2, 3)]
    assert funcs[0]["TestChild::test_case"] != funcs[1]["TestChild::test_case"]
    assert funcs[0]["TestA::test_case"] == funcs[1]["TestA::test_case"]


def test_a_deep_diamond_stays_small():
    """#1016 review r1: base versions are digested per level, so depth costs linear, not 2**n."""
    src = "class TestL0:\n    def test_x(self): pass\n" + "".join(
        f"class TestA{i}(TestL{i}):\n    pass\nclass TestB{i}(TestL{i}):\n    pass\n"
        f"class TestL{i + 1}(TestA{i}, TestB{i}):\n    pass\n"
        for i in range(40)
    )
    tracemalloc.start()
    deferred_nodes.test_functions(ast.parse(src))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 20_000_000


def test_a_base_change_leaves_a_test_the_class_defines_itself_unchanged():
    """#1009: no false change (more required targets) for a method the class overrides."""
    texts = [DIAMOND.format(2).replace("assert 1", f"assert {v}") for v in (1, 5)]
    funcs = [deferred_nodes.test_functions(ast.parse(t))[0] for t in texts]
    assert funcs[0]["TestB::test_case"] == funcs[1]["TestB::test_case"]


def test_a_plain_helper_class_is_not_read():
    funcs, bad = _nodes("class Helper:\n    def test_like(self): pass\n")
    assert funcs == set() and not bad


def test_a_case_subclass_with_init_is_uncollected_and_refused(project):
    layout = UT + "class FooTests(TestCase):\n" + INIT + M
    assert _nodes(layout)[1] == {"FooTests"}
    with pytest.raises(Rejected) as err:
        _new_tests(project, {"tests/test_regression.py": REGRESSION + layout})
    assert err.value.code == "DEFERRED_TESTS_NOT_COLLECTED"


def test_new_tests_sees_a_test_added_to_a_case_subclass(project):
    commit_change(project, {K: "import unittest\nclass FooTests(unittest.TestCase):\n    pass\n"})
    new = "import unittest\n" + HELD + "class FooTests(unittest.TestCase):\n" + M
    assert _new_tests(project, {K: new}) == {f"{K}::FooTests::test_m"}
