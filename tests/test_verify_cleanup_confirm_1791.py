"""#1791: the orphan cleanup says an attempt is cleaned only when its scratch directory is really
gone; otherwise the marker stays so a later run tries again, and the attempt is reported as kept."""

import os

import pytest

from cairnwork import verify_candidate as module
from cairnwork.intake import Intake

AID = "a0123456789abcdef"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="root removes any directory")


def _orphan(state, *, stuck):
    (state / "verify-runs").mkdir(parents=True)
    marker = state / "verify-runs" / AID
    marker.write_text("")
    scratch = state / "verify-scratch" / AID
    inner = scratch / "sub"
    inner.mkdir(parents=True)
    (inner / "f").write_text("x")
    if stuck:
        os.chmod(inner, 0o500)  # rmtree cannot empty it
    return marker, scratch, inner


def test_a_scratch_that_will_not_go_is_kept_and_reported(tmp_path):
    marker, scratch, inner = _orphan(tmp_path, stuck=True)
    try:
        out = module.cleanup(Intake(tmp_path))
        assert out["cleaned"] == []
        assert out["kept"] == [AID]
        assert marker.exists() and scratch.exists()
    finally:
        os.chmod(inner, 0o700)


def test_the_kept_attempt_is_cleaned_by_a_later_run_once_it_can_go(tmp_path):
    marker, scratch, inner = _orphan(tmp_path, stuck=True)
    module.cleanup(Intake(tmp_path))
    os.chmod(inner, 0o700)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists() and not scratch.exists()


def test_a_scratch_that_goes_is_cleaned_and_its_marker_removed(tmp_path):
    marker, scratch, _ = _orphan(tmp_path, stuck=False)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists() and not scratch.exists()
