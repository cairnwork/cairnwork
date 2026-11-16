"""Receipt and receiver-policy probes for #1353 C7-C8."""

from __future__ import annotations

import base64
import json
import sys

from _w1353_probe import row


def _c8_policy_cases(parent, state, p, check):
    from tests.attest.binding_helpers import notarize_bound, policy, sign_bound

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
            "verify_bundle",
            check(missing_bundle, policy=pol),
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
            check(policy=strict),
            "REJECTED",
            "REFUSED",
        )
    )
    return rows


def _c8_mutations(parent, state, p, bundle, check):
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
    from tests.attest.binding_helpers import policy, resign

    rows = []
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))

    def rechain(value):
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
        edited = json.loads(json.dumps(stmt))
        if field == "hidden_test_commit":
            edited["predicate"][field] = value
        else:
            edited["predicate"]["binding"][field] = value
        path = resign(state, rechain(edited), parent / f"{name}.json", rehash=True)
        if name == "blackbox_commitment":
            pinned = policy(
                state,
                parent,
                p["tests"],
                blackbox_commitment="sha256:" + "d" * 64,
                protection="partial_macos" if sys.platform == "darwin" else "partial",
            )
            observed = check(path, policy=pinned)
        else:
            observed = check(path)
        rows.append(row("C8", name, "verify_bundle", observed, wanted, "REFUSED"))
    rows.extend(_c8_policy_cases(parent, state, p, check))
    return rows


def _c8_v01(parent, state, p):
    from cairnwork_attest.verify import verify_bundle
    from tests.attest.binding_helpers import policy
    from tests.attest.test_verify import setup_verified_bundle_fixture

    legacy, pub, _, candidate = setup_verified_bundle_fixture(parent / "v01", p["work"])
    strict = policy(state, parent, p["tests"], protection="blackbox")
    result = verify_bundle(
        legacy, public_key_pem=pub, repo=p["work"], expected_candidate=candidate, policy=strict
    )
    return row(
        "C8",
        "v0.1-under-protected-policy",
        "verify_bundle",
        result,
        "TEST_SOURCE_MISMATCH",
        "REFUSED",
    )


def c8_rows(parent):
    from cairnwork_attest.verify import verify_bundle
    from tests.attest.binding_helpers import (
        notarize_bound,
        producer,
        receiver,
        sign_bound,
    )

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

    def check(path=bundle, **kw):
        kw.setdefault("expected_candidate", p["head"])
        return verify_bundle(path, public_key_pem=pub, repo=full, **kw)

    valid = check()
    rows.append(
        row(
            "C8",
            "valid",
            "verify_bundle",
            valid,
            "OK",
            "HONEST_VERIFIED" if valid.code == "OK" else "HONEST_FAILED",
        )
    )
    rows += [
        row(
            "C8",
            "head",
            "verify_bundle",
            check(expected_candidate=p["other"]),
            "HEAD_MISMATCH",
            "REFUSED",
        ),
        row(
            "C8", "base", "verify_bundle", check(expected_base="1" * 40), "BASE_MISMATCH", "REFUSED"
        ),
    ]
    rows.extend(_c8_mutations(parent, state, p, bundle, check))
    rows.append(_c8_v01(parent, state, p))
    return rows
