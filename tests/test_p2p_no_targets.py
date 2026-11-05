"""#793 Step 3: a base with test files under the roots whose module-dir selection holds none of
them. Its whole test root is the regression comparison (within the cap and the budget); a base
past either is UNCOMPARABLE ``P2P_NO_TARGETS``, never a silent "nothing to run". End to end
(run_task) on a real sandbox, plus the plan-level rules."""

from __future__ import annotations

import pytest
from conftest import commit_change
from p2p_side_stub import stub_side
from test_p2p_nodes import CALC, CONFTEST, MUL, MUL_TEST, SUB_OFF, _run
from test_p2p_plan import RESULT, TASK, context, plan

from cairnwork import p2p
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.p2p import P2PPlan, plan_p2p
from cairnwork.p2p_selection import MODULE_DIR_CAP

FAR = "import calc\ndef test_sub():\n    assert calc.sub(5, 3) == 2\n"  # not near src/calc.py
SLOW = "import time\ndef test_slow():\n    time.sleep(8)\n"
CHECK = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_mul.py")),)


def _task(repo, tmp_path, worker, far, **kw):
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, **far})
    tests = {"tests/test_mul.py": "import calc\n" + MUL_TEST}
    args = (repo, tmp_path, ("src/calc.py",), {"src/calc.py": worker + MUL}, tests, CHECK)
    return _run(*args, mode="module-dir", **kw)


@pytest.mark.sandbox
@pytest.mark.parametrize("good", [True, False], ids=["fix", "breaks-sub"])
def test_the_whole_base_guards_a_fix_with_no_nearby_test(repo, tmp_path, good):
    got = _task(repo, tmp_path, CALC if good else SUB_OFF, {"tests/unit/test_far.py": FAR})
    assert got.verified is good
    assert "P2P_NO_TARGETS" not in got.reason and got.p2p["fallback"] == "all-base-tests"


@pytest.mark.sandbox
def test_a_base_that_does_not_fit_the_budget_is_uncomparable(repo, tmp_path):
    got = _task(repo, tmp_path, CALC, {"tests/unit/test_slow.py": SLOW}, p2p_budget=2)
    assert not got.verified and "P2P_NO_TARGETS" in got.reason


@pytest.mark.sandbox
def test_a_base_past_the_cap_is_uncomparable(repo, tmp_path):
    far = {f"tests/unit/test_f{i:02}.py": FAR for i in range(MODULE_DIR_CAP + 1)}
    got = _task(repo, tmp_path, CALC, far)
    assert not got.verified and "P2P_NO_TARGETS" in got.reason


class Deleting(tuple):
    deleted = ("tests/test_gone.py",)


def test_a_file_the_test_commit_deletes_is_not_selected_after_a_degraded_probe(monkeypatch):
    """Whole-suite probe unusable, module-dir next: the deleted file stays out of both."""
    base = ("tests/test_gone.py", "tests/test_root.py", "tests/unit/test_deep.py")
    seen: list[tuple[str, ...]] = []
    probes = iter([(None, "base-probe-timeout"), ({"tests/test_root.py::t": "passed"}, "")])

    def fake_probe(task, files, result, ctx, budget):
        seen.append(files)
        return next(probes)

    monkeypatch.setattr(p2p, "_base_tests", lambda task, ex: tuple(f for f in base if f not in ex))
    monkeypatch.setattr(p2p, "_probe", fake_probe)
    stub_side(monkeypatch)
    got = plan_p2p(TASK, (), Deleting(), "whole-suite", 30, RESULT, context("/runs"))
    assert seen == [("tests/test_root.py", "tests/unit/test_deep.py"), ("tests/test_root.py",)]
    assert got.rule["degraded"] == "base-probe-timeout"


def test_nothing_selected_runs_the_whole_base_within_the_cap(monkeypatch):
    one = ({"tests/unit/test_x.py::t": "passed"}, "")
    got, probed = plan(monkeypatch, ("tests/unit/test_x.py",), [one], mode="module-dir")
    assert isinstance(got, P2PPlan) and probed == [("tests/unit/test_x.py",)]
    assert got.rule["fallback"] == "all-base-tests" and got.rule["targets"] == 1


def test_nothing_selected_in_a_base_with_no_test_runs_nothing(monkeypatch):
    got, probed = plan(monkeypatch, (), [], mode="module-dir")
    assert isinstance(got, P2PPlan) and probed == [] and got.rule["targets"] == 0


def test_nothing_selected_in_a_base_of_exactly_the_cap_runs_the_whole_base(monkeypatch):
    existing = tuple(f"tests/unit/test_x{i:02}.py" for i in range(MODULE_DIR_CAP))
    one = ({"tests/unit/test_x00.py::t": "passed"}, "")
    got, probed = plan(monkeypatch, existing, [one], mode="module-dir")
    assert isinstance(got, P2PPlan) and probed == [existing]
    assert got.rule["fallback"] == "all-base-tests"


def test_nothing_selected_in_a_base_over_the_cap_is_uncomparable(monkeypatch):
    existing = tuple(f"tests/unit/test_x{i:02}.py" for i in range(MODULE_DIR_CAP + 1))
    got, probed = plan(monkeypatch, existing, [], mode="module-dir")
    assert isinstance(got, BaseUncomparable) and probed == [] and "P2P_NO_TARGETS" in str(got)


def test_the_whole_base_over_the_budget_is_uncomparable(monkeypatch):
    slow = [(None, "base-probe-timeout")]
    got, probed = plan(monkeypatch, ("tests/unit/test_x.py",), slow, mode="module-dir")
    assert isinstance(got, BaseUncomparable) and len(probed) == 1
    assert "P2P_NO_TARGETS" in str(got) and "base-probe-timeout" in str(got)
