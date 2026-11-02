"""``verdict`` names the protection level beside VERIFIED; ``status`` is as before."""

import pytest
from verify_helpers import base_of, candidate, verify

from cairnwork import verify_candidate as vc


@pytest.mark.sandbox
def test_a_same_process_pass_says_so_in_the_verdict(state, project):
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    _, out = verify(state, project, base, sha, "--import-path", "src")
    assert out["status"] == "VERIFIED" and out["verified"] is True
    assert out["protection"] == "same_process_only"
    assert out["verdict"] == "VERIFIED same_process_only"


@pytest.mark.sandbox
def test_a_failed_run_keeps_the_status_as_the_verdict(state, project):
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return 0\n"})
    _, out = verify(state, project, base, sha, "--import-path", "src")
    assert out["status"] == "FAILED" and out["verdict"] == "FAILED"


@pytest.mark.parametrize(
    "level", ["blackbox", "partial", "same_process_only", "blackbox_macos", "partial_macos"]
)
def test_every_level_is_named_beside_verified(level):
    assert vc._status_fields(True, "FAILED", level) == {
        "status": "VERIFIED",
        "verdict": f"VERIFIED {level}",
    }
    assert vc._status_fields(False, "FAILED", level) == {"status": "FAILED", "verdict": "FAILED"}


def test_without_a_level_the_verdict_is_the_status():
    assert vc._status_fields(False, "UNCOMPARABLE", "")["verdict"] == "UNCOMPARABLE"
    assert vc._status_fields(True, "FAILED", "")["verdict"] == "VERIFIED"
