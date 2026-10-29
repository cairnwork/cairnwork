"""#430: #413's rule for v0.1 receipts. An observation of an id Core did not require refuses when
it is not PASS (``UNREQUIRED_OBSERVATION_FAILED``), the same check as a v0.2 receipt's
(binding_extras._unrequired_problem); a v0.1 receipt has no policy, so it applies as there with
none. v0.1 is not legacy only: every unbound run still produces it (statement.py). No producer
sends such an observation; the receipts are synthetic (a fake Core that always says VERIFIED),
modelling a buggy producer.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.verify import VerifyResult, verify_bundle
from tests.attest.binding_helpers import resign
from tests.attest.test_binding_unrequired_observation import FAILED, _edited
from tests.attest.test_verify import setup_verified_bundle_fixture


def _check(tmp_path: Path, repo: Path, edit: Any = None, **observed: Any) -> VerifyResult:
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo, **observed)
    if edit is not None:
        bundle = _edited({"bundle": bundle, "state": state}, tmp_path, edit)
    return verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)


@pytest.mark.parametrize("other", ["other", "blackbox:zzz"])
def test_an_unrequired_failed_observation_refuses(tmp_path, repo, other):
    out = _check(tmp_path, repo, unrequired=(other,), failing=(other,))
    assert out.code == FAILED, out
    assert f"[{other!r}]" in out.detail


@pytest.mark.parametrize("other", ["other", "blackbox:zzz"])
def test_an_unrequired_passed_observation_is_still_accepted(tmp_path, repo, other):
    """As for a v0.2 receipt without a commitment pin (#409's row U)."""
    assert _check(tmp_path, repo, unrequired=(other,)).code == "OK"


def test_a_required_failed_observation_is_not_this_check(tmp_path, repo):
    """A required id's status is Core's to judge (here a fake Core said VERIFIED): unchanged."""
    assert _check(tmp_path, repo, failing=("pytest",)).code == "OK"


def test_a_receipt_with_only_required_passed_observations_is_accepted(tmp_path, repo):
    assert _check(tmp_path, repo, required=("pytest", "pytest-1")).code == "OK"


@pytest.mark.parametrize("status", [None, "ERROR", "fail", "SKIP", ["FAIL"]], ids=repr)
def test_any_status_but_pass_refuses(tmp_path, repo, status):
    def edit(observations: list[dict[str, Any]]) -> None:
        other = next(o for o in observations if o["verifier_id"] == "other")
        if status is None:
            del other["status"]
        else:
            other["status"] = status

    assert _check(tmp_path, repo, edit, unrequired=("other",)).code == FAILED


def test_an_observation_that_is_not_an_object_returns_a_result(tmp_path, repo):
    """Skipped by this check, refused by the Core-verdict step; verify never raises."""
    out = _check(tmp_path, repo, lambda observations: observations.append("FAIL"))
    assert out.code == "RESPONSE_NOT_VERIFIED", out


@pytest.mark.parametrize("ids", [["pytest", 7], "pytest", {"pytest": 1}], ids=repr)
def test_required_ids_that_are_not_a_list_of_strings_require_nothing(tmp_path, repo, ids):
    """A signer writes required ids that are not a list of strings (``in`` would still find
    ``pytest`` in each) next to a FAIL observation of ``pytest``. Such ids do not say what was
    required (a v0.2 receipt refuses them as PROTECTION_LABEL_MISMATCH; a v0.1 one has no label),
    so that FAIL is an unrequired one. Core's echoed hashes no longer match, so the Core-verdict
    step would refuse too; this check comes first."""
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo, failing=("pytest",))
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    stmt["predicate"]["request"]["acceptance_contract"]["required_verifier_ids"] = ids
    bundle = resign(state, stmt, tmp_path / "edited.json", rehash=True)
    out = verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)
    assert out.code == FAILED, out
    assert "['pytest']" in out.detail
