"""#413: an observation of an id Core did not require refuses when it is not PASS, under any policy.

#373 (PR #409) refuses an unrequired ``blackbox:*`` observation under a commitment pin; the reason
the integration owner gave, that a FAIL the receiver was not asked about is still evidence that
something failed, holds for any id. No producer sends such an observation: pipeline_judge._contract
builds the required ids and the observations from the same runs and checks. The receipts are
synthetic (binding_helpers.notarize_bound, a fake Core that always says VERIFIED), modelling a
buggy producer; the observations are in the signed Core request, so a candidate cannot reach them.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.attest.test_binding_blackbox_checks import ALL, MISSING, PIN, _world, check, with_checks

FAILED = "UNREQUIRED_OBSERVATION_FAILED"
POLICIES: dict[str, dict[str, Any] | None] = {
    "no_policy": None,
    "no_pins": {},
    "older_pin": PIN,
    "pin_with_checks": with_checks(PIN),
}


@pytest.mark.parametrize("pins", POLICIES.values(), ids=POLICIES.keys())
def test_an_unrequired_failed_observation_refuses(tmp_path, pins):
    w = _world(tmp_path, ALL, unrequired=("other",), failing=("other",))
    out = check(w, pins)
    assert out.code == FAILED, out
    assert "['other']" in out.detail


@pytest.mark.parametrize("pins", POLICIES.values(), ids=POLICIES.keys())
def test_an_unrequired_passed_non_blackbox_observation_is_still_accepted(tmp_path, pins):
    w = _world(tmp_path, ALL, unrequired=("other",))
    assert check(w, pins).code == "OK"


@pytest.mark.parametrize(
    ("pins", "code"), [(None, FAILED), ({}, FAILED), (PIN, MISSING), (with_checks(PIN), MISSING)]
)
def test_an_unrequired_failed_blackbox_observation(tmp_path, pins, code):
    """Under a commitment pin #373's check reports it first, unchanged; without one this refuses."""
    w = _world(tmp_path, ALL, unrequired=("blackbox:zzz",), failing=("blackbox:zzz",))
    assert check(w, pins).code == code


def test_a_required_failed_observation_is_not_this_check(tmp_path):
    """A required id's status is Core's to judge (here a fake Core said VERIFIED): unchanged."""
    w = _world(tmp_path, ("pytest-0",), failing=("pytest-0",))
    assert check(w, None).code == "OK"
