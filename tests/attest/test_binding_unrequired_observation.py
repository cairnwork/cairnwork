"""#413: an observation of an id Core did not require refuses when it is not PASS, under any policy.

#373 (PR #409) refuses an unrequired ``blackbox:*`` observation under a commitment pin; the reason
the integration owner gave, that a FAIL the receiver was not asked about is still evidence that
something failed, holds for any id. No producer sends such an observation: pipeline_judge._contract
builds the required ids and the observations from the same runs and checks. The receipts are
synthetic (binding_helpers.notarize_bound, a fake Core that always says VERIFIED), modelling a
buggy producer; the observations are in the signed Core request, so a candidate cannot reach them.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest.bundle import read_bundle
from cairnwork_protocol import evidence_bundle_hash
from tests.attest.binding_helpers import notarize_bound, producer, receiver, resign, sign_bound
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


def _edited(w: dict, tmp_path: Path, edit: Any) -> Path:
    """The receipt with its evidence edited and re-signed by the producer's key; Core's echoed
    evidence hash is updated when it can still be computed, so only these checks can refuse."""
    stmt = json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))
    evidence = stmt["predicate"]["request"]["evidence_bundle"]
    edit(evidence["observations"])
    try:
        echoed = evidence_bundle_hash(evidence)
    except (KeyError, TypeError):
        echoed = None  # left as it was: the Core-verdict step refuses too
    if echoed is not None:
        stmt["predicate"]["response"]["hashes"]["evidence_bundle_hash"] = echoed
    return resign(w["state"], stmt, tmp_path / "edited.json", rehash=True)


@pytest.mark.parametrize(
    "status", [None, "ERROR", "fail", "SKIP", ["FAIL"], "pass", "PASS "], ids=repr
)
def test_any_status_but_pass_refuses(tmp_path, status):
    """Not only FAIL: an absent status (None here), another word, another case or another type."""
    w = _world(tmp_path, ALL, unrequired=("other",))

    def edit(observations: list[dict[str, Any]]) -> None:
        other = next(o for o in observations if o["verifier_id"] == "other")
        if status is None:
            del other["status"]
        else:
            other["status"] = status

    assert check(w, None, _edited(w, tmp_path, edit)).code == FAILED


def test_an_observation_that_is_not_an_object_returns_a_result(tmp_path):
    """Skipped by this check, refused by the Core-verdict step; verify never raises."""
    w = _world(tmp_path, ALL)
    out = check(w, None, _edited(w, tmp_path, lambda observations: observations.append("FAIL")))
    assert out.code == "RESPONSE_NOT_VERIFIED", out


def test_required_ids_that_are_not_strings_are_refused(tmp_path):
    """Required id 7 (an int) with a FAIL observation of id 7: the ids are not a list of strings,
    so they bear no label out and cannot make that FAIL a required one."""
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p, required=(7,), failing=(7,))  # type: ignore[arg-type]
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    w = {**p, "state": state, "bundle": bundle, "pub": state / "public.pem", "tmp": tmp_path}
    w["full"] = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    assert check(w, None).code == "PROTECTION_LABEL_MISMATCH"
