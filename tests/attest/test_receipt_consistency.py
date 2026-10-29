"""#440: the parts of a signed receipt must agree with each other, for v0.1 and v0.2 alike.

Core answers MISSING when the contract's and the plan's required verifier ids differ, and refuses a
request whose plan or evidence names another contract, change set or plan (nexus-core: product/
verification ``required_verifier_ids``; product/evidence ``integrity``: CROSS_BOUND, STALE,
CROSS_BINDING_INVALID). The hashes Core echoes cover each part, not that the parts name each other,
so offline verify compares them itself. ``predicate.verifier_observations`` is documented as an
exact copy of the request's observations, so a copy that differs refuses too. No producer writes
such a receipt (core_request.build_core_request builds every part from one list); the receipts
are synthetic, re-signed with the producer's key after a fake Core said VERIFIED (a buggy
producer).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest.bundle import read_bundle, write_bundle
from cairnwork_attest.dsse import _sign_bytes
from cairnwork_attest.keys import load_private_key
from cairnwork_attest.verify import VerifyResult, verify_bundle
from cairnwork_protocol import (
    acceptance_contract_hash,
    change_set_hash,
    evidence_bundle_hash,
    verification_plan_hash,
)
from tests.attest.binding_helpers import resign
from tests.attest.test_binding_blackbox_checks import _world, check
from tests.attest.test_verify import setup_verified_bundle_fixture

INCONSISTENT = "RECEIPT_INCONSISTENT"
Edit = Callable[[dict[str, Any]], None]  # edits the statement's predicate


def _chained(request: dict[str, Any]) -> None:
    """Point the plan and the evidence at the (edited) contract, change set and plan again."""
    plan, evidence = request["verification_plan"], request["evidence_bundle"]
    plan["acceptance_contract_hash"] = acceptance_contract_hash(request["acceptance_contract"])
    plan["change_set_hash"] = change_set_hash(request["change_set"])
    evidence["acceptance_contract_hash"] = plan["acceptance_contract_hash"]
    evidence["change_set_hash"] = plan["change_set_hash"]
    evidence["verification_plan_hash"] = verification_plan_hash(plan)


def _echoed(predicate: dict[str, Any]) -> None:
    """A fake Core that echoes the hashes of the request as it now is."""
    request, hashes = predicate["request"], predicate["response"]["hashes"]
    hashes["acceptance_contract_hash"] = acceptance_contract_hash(request["acceptance_contract"])
    hashes["change_set_hash"] = change_set_hash(request["change_set"])
    hashes["verification_plan_hash"] = verification_plan_hash(request["verification_plan"])
    hashes["evidence_bundle_hash"] = evidence_bundle_hash(request["evidence_bundle"])


def _resigned(state: Path, bundle: Path, out: Path, edit: Edit) -> Path:
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    edit(stmt["predicate"])
    _echoed(stmt["predicate"])
    return resign(state, stmt, out, rehash=True)


def _v01(tmp_path: Path, repo: Path, edit: Edit, **observed: Any) -> VerifyResult:
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo, **observed)
    bundle = _resigned(state, bundle, tmp_path / "edited.json", edit)
    return verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)


def _v02(tmp_path: Path, edit: Edit, required: tuple[str, ...], **kw: Any) -> VerifyResult:
    w = _world(tmp_path, required, blackbox_commitment="", **kw)
    return check(w, None, _resigned(w["state"], w["bundle"], tmp_path / "edited.json", edit))


def _plan_ids(ids: list[str]) -> Edit:
    def edit(predicate: dict[str, Any]) -> None:
        predicate["request"]["verification_plan"]["required_verifier_ids"] = ids
        _chained(predicate["request"])

    return edit


def test_contract_ids_beyond_the_plan_refuse_v01(tmp_path, repo):
    """The contract requires ``other`` (so its FAIL is a required one, #430 lets it by), the plan
    does not: Core would answer MISSING, never VERIFIED."""
    out = _v01(tmp_path, repo, _plan_ids(["pytest"]), required=("pytest", "other"),
               failing=("other",))  # fmt: skip
    assert out.code == INCONSISTENT, out
    assert "required_verifier_ids" in out.detail


def test_contract_ids_beyond_the_plan_refuse_v02(tmp_path):
    out = _v02(tmp_path, _plan_ids(["pytest-0"]), ("pytest-0", "other"), failing=("other",))
    assert out.code == INCONSISTENT, out


def test_plan_ids_beyond_the_contract_still_refuse_first_as_unrequired(tmp_path, repo):
    out = _v01(tmp_path, repo, _plan_ids(["other", "pytest"]), unrequired=("other",),
               failing=("other",))  # fmt: skip
    assert out.code == "UNREQUIRED_OBSERVATION_FAILED", out


def test_the_same_ids_in_another_order_are_consistent(tmp_path, repo):
    """Control: the edit and re-chaining alone change nothing; Core compares sets."""
    out = _v01(tmp_path, repo, _plan_ids(["pytest-1", "pytest"]), required=("pytest", "pytest-1"))
    assert out.code == "OK", out


@pytest.mark.parametrize("ids", [[7], [["pytest"]]], ids=repr)
def test_required_ids_that_are_not_strings_in_both_refuse(tmp_path, repo, ids):
    """Equal in contract and plan, but not ids: refused, never raised (a list is not hashable)."""

    def edit(predicate: dict[str, Any]) -> None:
        predicate["request"]["acceptance_contract"]["required_verifier_ids"] = ids
        _plan_ids(ids)(predicate)

    assert _v01(tmp_path, repo, edit).code == INCONSISTENT


CROSS = [
    ("verification_plan", "acceptance_contract_hash"),
    ("verification_plan", "change_set_hash"),
    ("evidence_bundle", "acceptance_contract_hash"),
    ("evidence_bundle", "change_set_hash"),
    ("evidence_bundle", "verification_plan_hash"),
]


def _cross(part: str, key: str) -> Edit:
    def edit(predicate: dict[str, Any]) -> None:
        request = predicate["request"]
        _chained(request)
        request[part][key] = "sha256:" + "0" * 64
        if part == "verification_plan":  # the evidence names the plan as it now is
            request["evidence_bundle"]["verification_plan_hash"] = verification_plan_hash(
                request["verification_plan"]
            )

    return edit


@pytest.mark.parametrize(("part", "key"), CROSS, ids=[f"{p}.{k}" for p, k in CROSS])
def test_a_part_naming_another_part_refuses_v01(tmp_path, repo, part, key):
    out = _v01(tmp_path, repo, _cross(part, key))
    assert out.code == INCONSISTENT, out
    assert f"{part}.{key}" in out.detail


@pytest.mark.parametrize(("part", "key"), CROSS, ids=[f"{p}.{k}" for p, k in CROSS])
def test_a_part_naming_another_part_refuses_v02(tmp_path, part, key):
    out = _v02(tmp_path, _cross(part, key), ("pytest-0",))
    assert out.code == INCONSISTENT, out


def _fail_in_copy(predicate: dict[str, Any]) -> None:
    extra = dict(predicate["verifier_observations"][0], verifier_id="other", status="FAIL")
    predicate["verifier_observations"].append(extra)


def _copy_dropped(predicate: dict[str, Any]) -> None:
    predicate["verifier_observations"].pop()


def _copy_status(predicate: dict[str, Any]) -> None:
    predicate["verifier_observations"][0]["status"] = "FAIL"


COPY = {"extra_fail": _fail_in_copy, "dropped": _copy_dropped, "status": _copy_status}


@pytest.mark.parametrize("edit", COPY.values(), ids=COPY.keys())
def test_a_copy_of_the_observations_that_differs_refuses_v01(tmp_path, repo, edit):
    """A receiver reading the copy would see a FAIL (or miss one) the request does not have."""
    out = _v01(tmp_path, repo, edit, required=("pytest", "pytest-1"))
    assert out.code == INCONSISTENT, out
    assert "verifier_observations" in out.detail


@pytest.mark.parametrize("edit", COPY.values(), ids=COPY.keys())
def test_a_copy_of_the_observations_that_differs_refuses_v02(tmp_path, edit):
    assert _v02(tmp_path, edit, ("pytest-0", "pytest-1")).code == INCONSISTENT


def test_a_copy_that_is_not_a_list_refuses(tmp_path, repo):
    out = _v01(tmp_path, repo, lambda predicate: predicate.pop("verifier_observations"))
    assert out.code == INCONSISTENT, out


def test_a_copy_that_is_not_canonical_json_refuses_without_raising(tmp_path, repo):
    """A float has no canonical JSON form: signed as raw bytes (statement_bytes refuses it)."""
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    stmt["predicate"]["verifier_observations"] = [0.5]
    raw = json.dumps(stmt).encode()
    bundle = tmp_path / "float.json"
    write_bundle(bundle, _sign_bytes(raw, load_private_key(state)))
    out = verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)
    assert out.code == INCONSISTENT, out
