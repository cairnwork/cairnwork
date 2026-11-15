"""#1105 point 2 wiring: ``cairnwork-verify`` output carries replay_set only when it refuses."""

import pytest
from verify_helpers import FIX, base_of, candidate, verify

from cairnwork import replay_set

BAD = "def add(a, b):\n    return a - b\n"


def test_failed_output_carries_replay_set(project, state):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": BAD})
    _, out = verify(state, project, base, cand)
    assert out["status"] == "FAILED"
    assert out["replay_set"]["candidate_commit"] == cand
    assert out["replay_set"]["base"] == base and out["replay_set"]["candidate_patch_sha256"]


def test_refused_output_has_no_replay_set(project, state):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": FIX, "src/other.py": "x = 1\n"})
    _, out = verify(state, project, base, cand)
    assert out["status"] == "REFUSED" and out["replay_set"] == {}


@pytest.mark.sandbox
def test_verified_output_has_no_replay_set(project, state):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": FIX})
    _, out = verify(state, project, base, cand)
    assert out["status"] == "VERIFIED" and out["replay_set"] == {}


def test_capture_failure_changes_nothing_else(project, state, monkeypatch):
    from cairnwork import verify_candidate as vc
    from cairnwork.intake import Intake

    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": BAD})
    verifiers = (("v", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),)

    def run():
        return vc.verify_candidate(
            Intake(state), repo=project, base=base, candidate=cand,
            allowed_files=("src/calc.py",), verifiers=verifiers,
        )  # fmt: skip

    plain = run()

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(replay_set, "run_git_bytes", boom)
    hurt = run()
    assert plain["status"] == "FAILED" and plain["replay_set"]["candidate_commit"] == cand
    assert hurt["replay_set"] == {"replay_set_error": "RuntimeError"}
    for key in ("status", "verified", "reason", "core_status", "candidate_commit", "judged_commit"):
        assert hurt[key] == plain[key], key
