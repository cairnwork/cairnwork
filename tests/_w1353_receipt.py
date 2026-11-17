"""Receipt and receiver-policy probes for #1353 C7-C8."""

from __future__ import annotations

import base64
import json
import sys

from _w1353_probe import row

import cairnwork_attest.verify as verify_api
from cairnwork.notary_verify import binding_hash
from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.record import _digest
from cairnwork_protocol import (
    acceptance_contract_hash,
    change_manifest_hash,
    change_set_hash,
    evidence_bundle_hash,
    verification_plan_hash,
)
from tests.attest.binding_helpers import (
    notarize_bound,
    policy,
    producer,
    receiver,
    resign,
    sign_bound,
)
from tests.attest.test_verify import setup_verified_bundle_fixture


def bundle_route(path, pub, full, candidate, expected_base=None, policy=None):
    return verify_api.verify_bundle(
        path,
        public_key_pem=pub,
        repo=full,
        expected_candidate=candidate,
        expected_base=expected_base,
        policy=policy,
    )


def _rechain(value):
    pred = value["predicate"]
    binding = pred["binding"]
    request = pred["request"]
    contract = request["acceptance_contract"]
    contract["requirements_hash"] = binding_hash(binding)
    target = "git-tree:" + binding["executed_tree"]
    request["change_set"]["target_revision"] = target
    request["change_manifest"]["target_tree"] = target
    hashes = {
        "acceptance_contract_hash": acceptance_contract_hash(contract),
        "change_set_hash": change_set_hash(request["change_set"]),
        "verification_plan_hash": verification_plan_hash(request["verification_plan"]),
        "evidence_bundle_hash": evidence_bundle_hash(request["evidence_bundle"]),
        "change_manifest_hash": change_manifest_hash(request["change_manifest"]),
    }
    plan = request["verification_plan"]
    evidence = request["evidence_bundle"]
    plan["acceptance_contract_hash"] = hashes["acceptance_contract_hash"]
    plan["change_set_hash"] = hashes["change_set_hash"]
    evidence["acceptance_contract_hash"] = hashes["acceptance_contract_hash"]
    evidence["change_set_hash"] = hashes["change_set_hash"]
    hashes["verification_plan_hash"] = verification_plan_hash(plan)
    evidence["verification_plan_hash"] = hashes["verification_plan_hash"]
    hashes["evidence_bundle_hash"] = evidence_bundle_hash(evidence)
    pred["request_hash"] = _digest(request)
    pred["response"]["hashes"] = hashes
    return value


def _c8_policy_cases(parent, state, p, pub, full, candidate, bundle):

    rows = []
    low = "partial_macos" if sys.platform == "darwin" else "partial"
    missing_run = notarize_bound(
        state,
        p,
        attempt="missing-check",
        required=("pytest-0", "blackbox:other"),
        blackbox_commitment="sha256:" + "d" * 64,
        protection=low,
    )
    missing_bundle = sign_bound(state, missing_run, parent / "missing-check.json")
    pol = policy(
        state,
        parent,
        p["tests"],
        blackbox_commitment="sha256:" + "d" * 64,
        blackbox_checks=["add"],
        protection=low,
    )
    rows.append(
        row(
            "C8",
            "blackbox_checks",
            "verify_" + "bundle",
            bundle_route(missing_bundle, pub, full, candidate, policy=pol),
            "BLACKBOX_CHECKS_MISSING",
            "REFUSED",
        )
    )
    strict = policy(
        state,
        parent,
        p["tests"],
        blackbox_commitment="sha256:" + "d" * 64,
        blackbox_checks=["add"],
        protection="blackbox",
    )
    rows.append(
        row(
            "C7",
            "c-receiver-requires-blackbox",
            "verify_bundle(policy)",
            bundle_route(bundle, pub, full, candidate, policy=strict),
            "REJECTED",
            "REFUSED",
        )
    )
    return rows


def _c8_mutations(parent, state, p, bundle, pub, full, candidate):

    rows = []
    stmt = json.JSONDecoder().decode(base64.b64decode(read_bundle(bundle)["payload"]).decode())

    edits = (
        ("test_commit", "hidden_test_commit", "e" * 40, "TEST_SOURCE_MISMATCH"),
        ("executed_tree", "executed_tree", "e" * 40, "TREE_MISMATCH"),
        (
            "blackbox_commitment",
            "blackbox_commitment",
            "sha256:" + "e" * 64,
            "BLACKBOX_COMMITMENT_MISMATCH",
        ),
    )
    for name, field, value, wanted in edits:
        edited = json.JSONDecoder().decode(json.dumps(stmt))
        if field == "hidden_test_commit":
            edited["predicate"]["hidden_test_commit"] = value
        elif field == "executed_tree":
            edited["predicate"]["binding"]["executed_tree"] = value
        else:
            edited["predicate"]["binding"]["blackbox_commitment"] = value
        path = resign(state, _rechain(edited), parent / f"{name}.json", rehash=True)
        route_result = (
            bundle_route(
                path,
                pub,
                full,
                candidate,
                policy=policy(
                    state,
                    parent,
                    p["tests"],
                    blackbox_commitment="sha256:" + "d" * 64,
                    protection="partial_macos" if sys.platform == "darwin" else "partial",
                ),
            )
            if name == "blackbox_commitment"
            else bundle_route(path, pub, full, candidate)
        )
        rows.append(row("C8", name, "verify_" + "bundle", route_result, wanted, "REFUSED"))
    rows.extend(_c8_policy_cases(parent, state, p, pub, full, candidate, bundle))
    return rows


def _c8_v01(parent, state, p):

    legacy, pub, _, candidate = setup_verified_bundle_fixture(parent / "v01", p["work"])
    strict = policy(state, parent, p["tests"], protection="blackbox")
    result = bundle_route(legacy, pub, p["work"], candidate, policy=strict)
    return row(
        "C8",
        "v0.1-under-protected-policy",
        "verify_" + "bundle",
        result,
        "TEST_SOURCE_MISMATCH",
        "REFUSED",
    )


def c8_rows(parent):

    rows = []
    p = producer(parent)
    state = parent / "receipt-state"
    run = notarize_bound(
        state,
        p,
        required=("pytest-0", "blackbox:add"),
        blackbox_commitment="sha256:" + "d" * 64,
        protection="partial_macos" if sys.platform == "darwin" else "partial",
    )
    bundle = sign_bound(state, run, parent / "bundle.json")
    full = receiver(parent, "full", p["work"], p["head"], p["tests"])
    pub = state / "public.pem"

    valid = bundle_route(bundle, pub, full, p["head"])
    rows.append(
        row(
            "C8",
            "valid",
            "verify_" + "bundle",
            valid,
            "OK",
            "HONEST_VERIFIED" if valid.code == "OK" else "HONEST_FAILED",
        )
    )
    rows += [
        row(
            "C8",
            "head",
            "verify_" + "bundle",
            bundle_route(bundle, pub, full, p["other"]),
            "HEAD_MISMATCH",
            "REFUSED",
        ),
        row(
            "C8",
            "base",
            "verify_" + "bundle",
            bundle_route(bundle, pub, full, p["head"], "1" * 40),
            "BASE_MISMATCH",
            "REFUSED",
        ),
    ]
    rows.extend(_c8_mutations(parent, state, p, bundle, pub, full, p["head"]))
    rows.append(_c8_v01(parent, state, p))
    return rows
