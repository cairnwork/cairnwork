"""#1105: the replay record is evidence only; building it never changes the verdict."""

import pytest
from verify_helpers import VERIFY, base_of, candidate

from cairnwork import replay_set
from cairnwork.intake import Intake
from cairnwork.verify_candidate import verify_candidate

sandbox = pytest.mark.sandbox
BAD = "def add(a, b):\n    return a - b\n"


def run(state, project, base, cand):
    return verify_candidate(
        Intake(state), repo=project, base=base, candidate=cand,
        allowed_files=("src/calc.py",), verifiers=(("v", tuple(VERIFY.split())),),
    )  # fmt: skip


@sandbox
def test_a_failing_record_build_leaves_the_verdict_unchanged(project, state, monkeypatch):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": BAD})
    normal = run(state, project, base, cand)
    assert normal["status"] == "FAILED" and "candidate_commit" in normal["replay_set"]

    def boom(*args, **kwargs):
        raise RuntimeError("no record")

    monkeypatch.setattr(replay_set, "capture", boom)
    broken = run(state, project, base, cand)
    assert broken["replay_set"] == {"replay_set_error": "RuntimeError"}
    verdict = ("verified", "status", "reason", "core_status", "binding", "judged_commit")
    assert {k: broken[k] for k in verdict} == {k: normal[k] for k in verdict}
