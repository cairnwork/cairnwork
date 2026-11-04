"""#793 Step 2: the base tests the test commit removed come from what pytest collected, not from
the AST reader. End to end (run_task, real sandbox), each shape with a fix that keeps the base
tests passing and a candidate that breaks an unchanged base test."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CONFTEST, MUL, MUL_TEST, SUB_OFF, _run

from cairnwork import p2p_collect
from cairnwork.collect_reader import Collected
from cairnwork.deferred_tests import BaseUncomparable, TestCommitRejected
from cairnwork.p2p_collect import classify, collected_side

pytestmark = pytest.mark.sandbox

CASE = (
    "import unittest, calc\n"
    "class Checks(unittest.TestCase):\n"
    "    def test_sub(self):\n"
    "        self.assertEqual(calc.sub(5, 3), 2)\n"
)
# the test commit only renames the base class: pytest still collects Checks::test_sub, the AST
# reader no longer sees the class
ALIASED = CASE.replace(
    "import unittest, calc\n", "import calc\nfrom unittest import TestCase as B\n"
)
ALIASED = ALIASED.replace("(unittest.TestCase)", "(B)")
MUL_CASE = "\ndef test_mul():\n    assert calc.mul(2, 3) == 6\n"
# the test commit hides a base test from pytest without removing it
HIDDEN = (
    "import calc\nif False:\n    def test_sub():\n        assert calc.sub(5, 3) == 2\n" + MUL_TEST
)
SUB_TEST = "import calc\ndef test_sub():\n    assert calc.sub(5, 3) == 2\n"
BROKEN = "import no_such_module_793\n" + SUB_TEST + MUL_TEST
# the base test_sub is inherited from a helper module: its name is never in tests/test_calc.py
HELPER = (
    "import calc\nclass SubChecks:\n    def test_sub(self):\n        assert calc.sub(5, 3) == 2\n"
)
INHERITS = "from checks_base import SubChecks\nclass TestCalc(SubChecks):\n    pass\n"
INHERIT_HIDES = {
    # the test commit stops collecting TestCalc::test_sub without removing its definition
    "dunder": "import calc\nfrom checks_base import SubChecks\nclass TestCalc(SubChecks):\n"
    "    __test__ = False\n" + MUL_TEST,
    "renamed": "import calc\nfrom checks_base import SubChecks\nclass CalcCases(SubChecks):\n"
    "    pass\n" + MUL_TEST,
}


def _case(repo, tmp_path, base_test, new_test, candidate):
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST,
                         "tests/test_calc.py": base_test})  # fmt: skip
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    return _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": candidate},
                {"tests/test_calc.py": new_test}, check)  # fmt: skip


@pytest.mark.parametrize("good", [True, False], ids=["fix", "breaks-sub"])
def test_a_test_the_ast_loses_but_pytest_collects_still_guards(repo, tmp_path, good):
    result = _case(repo, tmp_path, CASE, ALIASED + MUL_CASE, (CALC if good else SUB_OFF) + MUL)
    if good:
        assert result.status == "COMPLETED" and result.verified, result.reason
    else:  # main: verified (the AST said "removed"), the regression in test_sub unseen
        assert result.status == "FAILED" and not result.verified
        assert "tests/test_calc.py::Checks::test_sub" in result.p2p["rerun"]["failed"]


@pytest.mark.parametrize("good", [True, False], ids=["fix", "breaks-sub"])
def test_a_base_test_hidden_from_pytest_refuses_the_test_commit(repo, tmp_path, good):
    result = _case(repo, tmp_path, SUB_TEST, HIDDEN, (CALC if good else SUB_OFF) + MUL)
    assert not result.verified  # main: verified both ways (test_sub was "removed")
    assert "TEST_COMMIT_DROPS_BASE_TESTS" in result.reason


@pytest.mark.parametrize("shape", sorted(INHERIT_HIDES))
@pytest.mark.parametrize("good", [True, False], ids=["fix", "breaks-sub"])
def test_an_inherited_base_test_the_test_commit_stops_collecting_is_refused(
    repo, tmp_path, shape, good
):
    files = {"src/calc.py": CALC, "tests/conftest.py": CONFTEST}
    commit_change(repo, {**files, "tests/checks_base.py": HELPER, "tests/test_calc.py": INHERITS})
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    candidate = (CALC if good else SUB_OFF) + MUL
    result = _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": candidate},
                  {"tests/test_calc.py": INHERIT_HIDES[shape]}, check)  # fmt: skip
    # before #838's fix: COMPLETED and verified (the name was "gone" from test_calc.py)
    assert not result.verified
    assert "TEST_COMMIT_DROPS_BASE_TESTS" in result.reason


def test_an_unreadable_collect_run_is_uncomparable(monkeypatch, tmp_path):
    task: Any = SimpleNamespace(repo=Path("/r"), base_commit="b" * 40, test_commit="c" * 40)
    log = tmp_path / "run" / "log"
    log.parent.mkdir()
    monkeypatch.setattr(p2p_collect, "_present", lambda task, files: files)
    monkeypatch.setattr(
        p2p_collect, "run_verifiers", lambda *a, **k: [SimpleNamespace(log_path=str(log))]
    )
    monkeypatch.setattr(p2p_collect, "read_collected", lambda run: Collected(unusable="no junit"))
    with pytest.raises(BaseUncomparable) as caught:
        collected_side(task, ("tests/test_calc.py",), {"tests/test_calc.py::test_sub": "passed"},
                       ("tests/test_calc.py",), None, 30, "a")  # fmt: skip
    assert caught.value.comparison.kind == "UNCOMPARABLE"
    assert caught.value.comparison.code == "P2P_TEST_COLLECT_UNUSABLE"
    assert not log.parent.exists()  # the run's evidence is removed


@pytest.mark.parametrize("kind", ["error_files", "skipped_files"])
def test_a_base_test_in_a_file_that_failed_to_collect_is_refused_not_removed(monkeypatch, kind):
    # the deferred base check refuses an import error first (BASE_COLLECTION_ERROR); this pins
    # the P2P side on its own: unknown ids are never read as removed
    task: Any = SimpleNamespace(repo=Path("/r"), base_commit="b" * 40, test_commit="c" * 40)
    files: Any = {kind: ("tests/test_calc.py",)}
    got = Collected(ids=(), **files)
    monkeypatch.setattr(p2p_collect, "_present", lambda task, files: files)
    monkeypatch.setattr(p2p_collect, "_collect", lambda *a: got)
    with pytest.raises(TestCommitRejected) as caught:
        collected_side(task, ("tests/test_calc.py",), {"tests/test_calc.py::test_sub": "passed"},
                       ("tests/test_calc.py",), None, 30, "a")  # fmt: skip
    assert caught.value.code == "TEST_COMMIT_COLLECT_ERROR"


def test_the_rule_binds_the_collected_sets(repo, tmp_path):
    result = _case(repo, tmp_path, SUB_TEST, SUB_TEST + MUL_TEST, CALC + MUL)
    assert result.verified, result.reason
    rule = result.p2p
    base = ["tests/test_calc.py::test_sub"]
    test = sorted([*base, "tests/test_calc.py::test_mul"])
    assert rule["base_ran"] == 1 and rule["test_collected"] == 2 and rule["removed"] == 0
    assert rule["base_ran_sha256"] == hashlib.sha256("\n".join(base).encode()).hexdigest()
    assert rule["test_collected_sha256"] == hashlib.sha256("\n".join(test).encode()).hexdigest()


BASE_AB = "def test_a(): pass\ndef test_b(): pass\n"


@pytest.mark.parametrize(
    ("edited", "source", "base", "removed", "dropped"),
    [
        ({"t.py"}, "def test_b(): pass\n", BASE_AB, {"t.py::test_a"}, set()),  # edited, name gone
        ({"t.py"}, "if 0:\n    def test_a(): pass\n", BASE_AB, set(), {"t.py::test_a"}),
        (set(), "", BASE_AB, set(), {"t.py::test_a"}),  # the file was not edited
        # the base file never defined test_a itself (inherited / generated): not provably removed
        ({"t.py"}, "def test_b(): pass\n", "from h import *\n", set(), {"t.py::test_a"}),
        (
            {"t.py"},
            "def test_b(): pass\n",
            "class C:\n    def test_a(self): pass\n",
            set(),
            {"t.py::test_a"},
        ),  # defined in a class, not at module level
    ],
)
def test_classify_removes_only_a_base_defined_test_whose_name_is_gone(
    edited, source, base, removed, dropped
):
    side = classify({"t.py::test_a", "t.py::test_b"}, {"t.py::test_b"}, edited,
                    lambda p: source, lambda p: base)  # fmt: skip
    assert side.removed == removed and side.dropped == dropped


def test_classify_matches_the_class_of_a_class_test():
    base = "class A:\n    def test_x(self): pass\nclass B:\n    pass\n"
    ids = {"t.py::A::test_x", "t.py::B::test_x"}
    side = classify(ids, set(), {"t.py"}, lambda p: "", lambda p: base)
    assert side.removed == {"t.py::A::test_x"} and side.dropped == {"t.py::B::test_x"}


@pytest.mark.parametrize(
    "base",
    [
        # a module-level def does not define the class's test, and the class is redefined
        "def test_a(): pass\nclass A(M): pass\n",
        # the later class A (what pytest collects) has no def test_a, the earlier one had
        "class A:\n    def test_a(self): pass\nclass A(M): pass\n",
    ],
)
def test_classify_drops_a_test_the_last_class_of_that_name_does_not_define(base):
    side = classify({"t.py::A::test_a"}, set(), {"t.py"}, lambda p: "", lambda p: base)
    assert side.removed == set() and side.dropped == {"t.py::A::test_a"}
