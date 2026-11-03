"""blind_warn splits a candidate failure by the base run: regression or not fixed (display only)."""

from types import SimpleNamespace
from typing import cast

from cairnwork.verifiers import VerifierRun
from experimental.blind_warn import judge

NODE = "tests/test_blind_a.py::"


def run_of(outcomes: dict[str, str], rc: int = 1) -> VerifierRun:
    return cast(VerifierRun, SimpleNamespace(returncode=rc, outcomes=outcomes, collect_errors=()))


def test_a_function_that_passed_on_the_base_is_a_regression_and_one_that_failed_is_not_fixed():
    base = run_of(
        {NODE + "test_reg": "passed", NODE + "test_bug": "failed", NODE + "test_ok": "passed"}
    )
    cand = run_of(
        {NODE + "test_reg": "failed", NODE + "test_bug": "failed", NODE + "test_ok": "passed"}
    )
    got = judge._summary(cand, base)
    assert got["failed"] == ["test_bug", "test_reg"]
    assert got["regression"] == ["test_reg"] and got["not_fixed"] == ["test_bug"]


def test_a_base_error_or_skip_counts_as_not_fixed_and_a_mixed_parametrized_name_too():
    base = run_of({NODE + "test_e": "error", NODE + "test_s": "skipped",
                   NODE + "test_p[1]": "passed", NODE + "test_p[2]": "failed"})  # fmt: skip
    cand = run_of({NODE + "test_e": "failed", NODE + "test_s": "failed",
                   NODE + "test_p[1]": "failed", NODE + "test_p[2]": "failed"})  # fmt: skip
    got = judge._summary(cand, base)
    assert got["regression"] == [] and got["not_fixed"] == ["test_e", "test_p", "test_s"]


def test_the_line_counts_each_kind_and_keeps_its_ends():
    a = {"failed": ["test_bug", "test_reg"], "regression": ["test_reg"], "not_fixed": ["test_bug"]}
    b = {"failed": ["test_bug"], "regression": [], "not_fixed": ["test_bug"]}
    line = judge._line(a, b)
    assert line == (
        "Blind tests: A and B both fail (test_bug, test_reg); not fixed: 1, regression: 1; "
        "not a verdict"
    )
    only = judge._line({**b, "failed": ["t"], "not_fixed": ["t"]}, b)
    assert "regression" not in only and "not fixed: 2" in only


def test_the_split_does_not_change_warn_or_the_failed_list():
    base = run_of({NODE + "test_x": "failed"})
    cand = run_of({NODE + "test_x": "failed"})
    failed, undecided, written = judge.classify(cand, base)
    assert (failed, undecided, written) == (["test_x"], [], 1)
    assert judge._summary(cand, base)["failed"] == failed
    empty = judge._result()
    assert empty["a"]["regression"] == [] and empty["a"]["not_fixed"] == []
