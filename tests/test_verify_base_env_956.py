"""#956 item 1: in a verify call without deferred tests the base check only explains a failing
candidate (#259); it has no later base run to bind. Two of the #929/#930 refusals carry no
evidence about the environment there, so they no longer turn a failing candidate's FAILED into
UNCOMPARABLE: a base test file no verifier target can name is left out of the check, and a check
run that the base's own config stopped early is no environment failure (and no pass to
remember). With deferred tests both are still refused (tests/test_p2p_base_complete.py)."""

from types import SimpleNamespace

import pytest
from conftest import commit_change
from verify_helpers import FIX, candidate, in_process

from cairnwork import p2p
from cairnwork.deferred_tests import BaseUncomparable

pytestmark = pytest.mark.sandbox
WRONG = "def add(a, b):\n    return a + b + 2\n"  # still fails tests/test_calc.py
FAR = {"tests/v1.0/test_far.py": "def test_far():\n    assert True\n"}  # no target can name it
STOPS = "\n\ndef pytest_configure(config):\n    config.option.maxfail = 1\n"


def _stop_early(project):
    text = (project / "tests" / "conftest.py").read_text()
    return commit_change(project, {"tests/conftest.py": text + STOPS})


@pytest.mark.parametrize("cand", [WRONG, FIX], ids=["wrong", "fixed"])
def test_an_untargetable_base_file_does_not_hide_the_candidates_verdict(project, state, cand):
    base = commit_change(project, FAR)
    out = in_process(project, state, base, candidate(project, base, {"src/calc.py": cand}))
    assert out["status"] == ("FAILED" if cand == WRONG else "VERIFIED"), out
    assert out["uncomparable"] is None and out["core_status"]  # Core was asked
    assert "base-probe" not in out["reason"]


@pytest.mark.parametrize("cand", [WRONG, FIX], ids=["wrong", "fixed"])
def test_an_early_stopped_base_check_does_not_hide_the_candidates_verdict(project, state, cand):
    base = _stop_early(project)  # test_calc.py fails on the base, so test_skip.py never runs
    out = in_process(project, state, base, candidate(project, base, {"src/calc.py": cand}))
    assert out["status"] == ("FAILED" if cand == WRONG else "VERIFIED"), out
    assert out["uncomparable"] is None and out["core_status"]
    assert not list(state.rglob("base-env-ok/*"))  # nothing remembered: probed again next time


def test_a_base_that_cannot_run_its_tests_is_still_uncomparable_beside_a_far_file(project, state):
    """The left-out file changes nothing else: a broken base still explains a failing candidate."""
    base = commit_change(project, {**FAR, "tests/conftest.py": "raise RuntimeError('broken')\n"})
    out = in_process(project, state, base, candidate(project, base, {"src/calc.py": WRONG}))
    assert out["status"] == "UNCOMPARABLE" and out["uncomparable"]["code"] == "BASE_ENV_FAILED"


def test_the_strict_selection_is_unchanged(project):  # deferred tests and plan_p2p: still refused
    base = commit_change(project, FAR)
    task = SimpleNamespace(repo=project, base_commit=base, test_roots=("tests",))
    with pytest.raises(BaseUncomparable) as info:
        p2p._base_tests(task, frozenset())
    assert info.value.comparison.code == "P2P_BASE_UNTARGETABLE"
    assert "tests/v1.0/test_far.py" not in p2p._base_tests(task, frozenset(), strict=False)
    assert "tests/test_calc.py" in p2p._base_tests(task, frozenset(), strict=False)
