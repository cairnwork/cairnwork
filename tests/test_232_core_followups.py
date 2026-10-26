"""#232 follow-up tests for cairnwork core."""

from __future__ import annotations

import subprocess

import pytest
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from verify_helpers import FIX, candidate

from cairnwork.diffcheck import _known_kind, diff_check_detail
from cairnwork.intake import Intake
from cairnwork.notary import Notary
from cairnwork.verify_candidate import verify_candidate

sandbox = pytest.mark.sandbox


@sandbox
def test_verify_candidate_bound_head_verdict(
    project, state, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #232: verify_candidate queries notary verdict with binding["head"] instead of judged commit
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    cand = candidate(project, base, {"src/calc.py": FIX})
    verifier = tuple(CHECK[0][1])

    queried_candidates: list[str | None] = []
    orig_verdict = Notary.verdict

    def spy_verdict(self: Notary, task_id: str, attempt_id: str, expected_candidate: str):
        queried_candidates.append(expected_candidate)
        return orig_verdict(self, task_id, attempt_id, expected_candidate=expected_candidate)

    monkeypatch.setattr(Notary, "verdict", spy_verdict)

    out = verify_candidate(
        Intake(state),
        repo=project,
        base=base,
        candidate=cand,
        allowed_files=("src/calc.py",),
        verifiers=(("v", verifier),),
        test_commit=test_commit,
    )
    assert out["verified"] is True
    assert out["status"] == "VERIFIED"
    assert out["core_status"] == "VERIFIED"
    assert out["reason"] == "CORE_VERIFIED"
    assert out["binding"] is not None
    assert out["binding"]["head"] == cand
    assert out["judged_commit"] != cand
    assert queried_candidates[-1] == cand


def test_diffcheck_kind_without_trailing_period() -> None:
    # #232: kind must end with a period; without trailing period it is not a known kind
    assert _known_kind("trailing whitespace.") is True
    assert _known_kind("trailing whitespace") is False
    assert _known_kind("trailing whitespace ") is False
    assert _known_kind("trailing whitespace..") is False  # only one final period is stripped (R4)

    # In diff_check_detail, header without trailing period falls back to base
    cause = subprocess.CalledProcessError(
        returncode=1,
        cmd=["git", "diff", "--check"],
        output=b"src/a.py:1: trailing whitespace\n",
    )
    assert diff_check_detail("base_reason", cause, ("src/a.py",)) == "base_reason"

    cause_space = subprocess.CalledProcessError(
        returncode=1,
        cmd=["git", "diff", "--check"],
        output=b"src/a.py:1: trailing whitespace \n",
    )
    assert diff_check_detail("base_reason", cause_space, ("src/a.py",)) == "base_reason"
