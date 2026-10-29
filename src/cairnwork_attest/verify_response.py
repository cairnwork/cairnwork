"""The verification result type and the Core-response check shared by both statement versions
(v0.1 in verify.py, v0.2 in verify_binding.py); split out of verify.py for the 400-line cap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cairnwork_protocol import (
    acceptance_contract_hash,
    canonical_json,
    change_manifest_hash,
    change_set_hash,
    evidence_bundle_hash,
    response_binding_problem,
    verification_plan_hash,
)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    code: str
    detail: str = ""
    statement: dict[str, Any] | None = None
    assurance: dict[str, Any] | None = None  # v0.2 (#229): what the receiver itself checked

    def __bool__(self) -> bool:
        return self.ok


def _fail(code: str, detail: str = "") -> VerifyResult:
    return VerifyResult(ok=False, code=code, detail=detail)


def _check_response_verified(
    predicate: dict[str, Any],
    core_identity_pin: Mapping[str, Any] | None = None,
) -> VerifyResult | None:
    response = predicate.get("response")
    if not isinstance(response, dict):
        return _fail("RESPONSE_NOT_VERIFIED", "response is not an object")

    verif = response.get("verification")
    if not isinstance(verif, dict):
        return _fail("RESPONSE_NOT_VERIFIED", "response missing verification object")

    if verif.get("status") != "VERIFIED" or verif.get("integrity") != "VALID":
        return _fail(
            "RESPONSE_NOT_VERIFIED",
            f"status={verif.get('status')!r}, integrity={verif.get('integrity')!r}",
        )

    if predicate.get("status") != "VERIFIED" or predicate.get("integrity") != "VALID":
        st = predicate.get("status")
        integ = predicate.get("integrity")
        return _fail("RESPONSE_NOT_VERIFIED", f"predicate status={st!r}, integrity={integ!r}")

    if predicate.get("http_status") != 200:
        return _fail("RESPONSE_NOT_VERIFIED", f"http_status={predicate.get('http_status')!r}")

    pred_core_id = predicate.get("core_identity")
    resp_core_id = response.get("core_identity")
    if not isinstance(pred_core_id, Mapping) or not isinstance(resp_core_id, Mapping):
        return _fail("RESPONSE_NOT_VERIFIED", "response or predicate missing core_identity")

    try:
        if canonical_json(pred_core_id) != canonical_json(resp_core_id):
            return _fail("RESPONSE_NOT_VERIFIED", "predicate and response core_identity mismatch")
    except TypeError:
        return _fail("RESPONSE_NOT_VERIFIED", "core_identity not canonical JSON serializable")

    if core_identity_pin is not None:
        if not isinstance(core_identity_pin, Mapping):
            return _fail("CORE_IDENTITY_MISMATCH", "core_identity_pin must be a Mapping")
        try:
            if canonical_json(core_identity_pin) != canonical_json(resp_core_id):
                return _fail(
                    "CORE_IDENTITY_MISMATCH",
                    "response core_identity does not match pin: "
                    f"{resp_core_id!r} != {core_identity_pin!r}",
                )
        except TypeError:
            return _fail(
                "CORE_IDENTITY_MISMATCH", "core_identity_pin not canonical JSON serializable"
            )

    request = predicate["request"]
    binding_problem = response_binding_problem(request, response, resp_core_id)
    if binding_problem is not None:
        return _fail("RESPONSE_NOT_VERIFIED", f"response binding problem: {binding_problem}")

    hashes = response.get("hashes")
    if not isinstance(hashes, dict):
        return _fail("RESPONSE_NOT_VERIFIED", "response missing hashes object")

    try:
        expected = {
            "acceptance_contract_hash": acceptance_contract_hash(request["acceptance_contract"]),
            "change_set_hash": change_set_hash(request["change_set"]),
            "verification_plan_hash": verification_plan_hash(request["verification_plan"]),
            "evidence_bundle_hash": evidence_bundle_hash(request["evidence_bundle"]),
            "change_manifest_hash": change_manifest_hash(request["change_manifest"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        return _fail("RESPONSE_NOT_VERIFIED", f"malformed request for hashes: {exc}")

    for key, exp_val in expected.items():
        if hashes.get(key) != exp_val:
            return _fail("RESPONSE_NOT_VERIFIED", f"hash mismatch for {key}: expected {exp_val}")

    return _parts_problem(predicate, request, expected)


_CROSS = (  # (part, the hash it names): nexus-core product/evidence ``integrity``
    ("verification_plan", "acceptance_contract_hash"),
    ("verification_plan", "change_set_hash"),
    ("evidence_bundle", "acceptance_contract_hash"),
    ("evidence_bundle", "change_set_hash"),
    ("evidence_bundle", "verification_plan_hash"),
)


def _parts_problem(
    predicate: Mapping[str, Any], request: Mapping[str, Any], hashes: Mapping[str, str]
) -> VerifyResult | None:
    """#440: the hashes Core echoes cover each part of the request, not that the parts name each
    other. Core refuses a request whose plan or evidence names another contract, change set or
    plan (CROSS_BOUND, STALE, CROSS_BINDING_INVALID) or whose contract and plan require different
    ids (MISSING), so a VERIFIED response to one is not its verdict; and
    ``predicate.verifier_observations`` is an exact copy of the request's observations. No producer
    writes either (core_request.build_core_request builds every part from one list): this guards
    against a producer bug, a lying signer can write consistent parts. The parts are objects here:
    their hashes were computed above."""
    for part, key in _CROSS:
        if request[part].get(key) != hashes[key]:
            return _fail("RECEIPT_INCONSISTENT", f"{part}.{key} is not this request's {key}")
    ids = [
        request[p].get("required_verifier_ids")
        for p in ("acceptance_contract", "verification_plan")
    ]
    strings = all(isinstance(i, list) and all(isinstance(v, str) for v in i) for i in ids)
    if not strings or set(ids[0]) != set(ids[1]):
        return _fail(
            "RECEIPT_INCONSISTENT", f"contract and plan required_verifier_ids differ: {ids}"
        )
    try:
        copied = canonical_json(predicate.get("verifier_observations"))
        same = copied == canonical_json(request["evidence_bundle"]["observations"])
    except TypeError:
        same = False
    if not same:
        return _fail("RECEIPT_INCONSISTENT", "predicate.verifier_observations is not the request's")
    return None
