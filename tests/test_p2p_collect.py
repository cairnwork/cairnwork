"""#793 Step 2: the base tests the test commit removed come from what pytest collected, not from
the AST reader. End to end (run_task, real sandbox), each shape with a fix that keeps the base
tests passing and a candidate that breaks an unchanged base test."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CONFTEST, MUL, MUL_TEST, SUB_OFF, _run

from cairnwork import p2p_collect
from cairnwork.collect_reader import Collected
from cairnwork.deferred_tests import TestCommitRejected
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
    assert rule["collected_base"] == 1 and rule["collected_test"] == 2 and rule["removed"] == 0
    assert len(rule["collected_base_sha256"]) == 64


@pytest.mark.parametrize(
    ("edited", "source", "removed", "dropped"),
    [
        ({"t.py"}, "def test_b(): pass\n", {"t.py::test_a"}, set()),  # edited, name gone
        ({"t.py"}, "if 0:\n    def test_a(): pass\n", set(), {"t.py::test_a"}),  # name still there
        (set(), "", set(), {"t.py::test_a"}),  # the file was not edited
    ],
)
def test_classify_removes_only_an_edited_file_whose_name_is_gone(edited, source, removed, dropped):
    side = classify({"t.py::test_a", "t.py::test_b"}, {"t.py::test_b"}, edited, lambda p: source)
    assert side.removed == removed and side.dropped == dropped
