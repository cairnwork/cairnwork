"""#303 keystone: Core, not Cairnwork, keeps a failed or missing blackbox check from VERIFIED.

judge_candidate adds each check as an observation and as a required verifier id; Core's definition
of VERIFIED is unchanged. These run verify_candidate in-process against the real Core with the
blackbox run replaced by a fixed verdict (the account path itself is test_blackbox_account.py):
a failed check is not VERIFIED, a declared check whose observation never reaches Core is not
VERIFIED, and only when every check passes is the candidate VERIFIED.
"""

import secrets
from typing import Any

import pytest
from verify_helpers import FIX, VERIFY, base_of, candidate

from cairnwork import pipeline_judge
from cairnwork.blackbox import Case, Check, Declaration
from cairnwork.blackbox_judge import CheckVerdict
from cairnwork.core_request import VerifierObservation
from cairnwork.intake import Intake
from cairnwork.notary import Notary
from cairnwork.verify_candidate import verify_candidate

DECLARED = Declaration(
    secrets.token_hex(32), (Check("add", "calc", "add", (Case("c1", "[2,3]", "{}", "5"),)),)
)


def fixed(passed: bool):
    def judge(declaration, repo, commit, run_root, run_id, **kwargs):
        observation = VerifierObservation(
            "blackbox:add", "x/blackbox.log#add", "sha256:" + "a" * 64, passed
        )
        return (
            CheckVerdict(observation, f"blackbox:add: {'PASS' if passed else 'MISMATCH'}"),
        ), None

    return judge


def run(state, project) -> dict[str, Any]:
    base = base_of(project)
    fix = candidate(project, base, {"src/calc.py": FIX})
    return verify_candidate(
        Intake(state), repo=project, base=base, candidate=fix, allowed_files=("src/calc.py",),
        verifiers=(("pytest-0", tuple(VERIFY.split())),), blackbox=DECLARED,
    )  # fmt: skip


@pytest.mark.parametrize("passed", [False, True], ids=["check-failed", "all-passed"])
def test_core_decides_with_the_check(state, project, monkeypatch, passed):
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(passed))
    out = run(state, project)
    assert out["verified"] is passed
    assert out["core_status"] == ("VERIFIED" if passed else "FAILED_VERIFICATION"), out
    assert ("MISMATCH" in out["reason"]) is not passed


def test_a_check_that_never_reaches_core_is_not_verified(state, project, monkeypatch):
    """Simulate a wiring slip: the check passed, but its observation is dropped on the way."""
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", fixed(True))
    real = Notary.notarize

    def drop(self, task_id, attempt_id, spec, repo, base, judged, observations, binding=None):
        kept = tuple(o for o in observations if not o.verifier_id.startswith("blackbox:"))
        assert "blackbox:add" in spec.required_verifier_ids and len(kept) < len(observations)
        return real(self, task_id, attempt_id, spec, repo, base, judged, kept, binding)

    monkeypatch.setattr(Notary, "notarize", drop)
    out = run(state, project)
    assert not out["verified"] and out["core_status"] == "UNVERIFIABLE", out


def test_without_a_declaration_the_request_is_unchanged(project):
    from cairnwork.pipeline import TaskSpec

    plain = TaskSpec("k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), ())
    declared = TaskSpec(
        "k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), (), blackbox=DECLARED
    )
    assert set(plain.request()) == {
        "repo", "title", "instructions", "base_commit", "allowed_files", "verifiers",
        "allow_deletions",
    }  # fmt: skip
    assert declared.request() == plain.request() | {"blackbox_commitment": DECLARED.commitment()}


def test_the_receipt_binding_carries_the_commitment_only_when_declared(project):
    from cairnwork.pipeline import TaskSpec
    from cairnwork.verify_candidate import _binding

    head = base_of(project)
    plain = TaskSpec("k", "h", "t", "i", project, head, ("src/calc.py",), (), test_commit="b" * 40)
    declared = TaskSpec(
        "k", "h", "t", "i", project, head, ("src/calc.py",), (), test_commit="b" * 40,
        blackbox=DECLARED,
    )  # fmt: skip
    without, with_ = _binding(plain, head, (), head), _binding(declared, head, (), head)
    assert "blackbox_commitment" not in without
    assert with_ == without | {"blackbox_commitment": DECLARED.commitment()}


def test_a_check_id_that_clashes_with_a_verifier_is_refused(project):
    from cairnwork.pipeline import TaskRejected, TaskSpec, check_task

    verifiers = (("blackbox:add", tuple(VERIFY.split())),)
    task = TaskSpec(
        "k", "h", "t", "i", project, "a" * 40, ("src/calc.py",), verifiers, blackbox=DECLARED
    )
    with pytest.raises(TaskRejected, match="clash"):
        check_task(task)


def test_a_declared_check_that_yields_no_verdict_stays_required(state, project, monkeypatch):
    monkeypatch.setattr(pipeline_judge, "judge_blackbox", lambda *a, **k: ((), None))
    out = run(state, project)
    assert not out["verified"] and out["core_status"] == "UNVERIFIABLE", out
