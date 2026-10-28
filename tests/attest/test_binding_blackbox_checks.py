"""#312: a receiver that pins ``blackbox_commitment`` checks which declared checks Core required.

The commitment names the declaration but not its check ids, so the policy carries them too
(``blackbox_checks``, what ``cairnwork-verify blackbox-commitment`` prints next to the commitment).
The receipts are synthetic: binding_helpers.notarize_bound with a fake Core that always says
VERIFIED, signed with the producer's own key, the required ids chosen per row to model a buggy
producer that bound a declaration's commitment without requiring each of its checks. A candidate
cannot reach any of this: the ids are in the signed Core request, covered by the binding hash.

An older policy (a pinned commitment without ``blackbox_checks``, James's option (a) on 2026-10-28)
only needs at least one ``blackbox:*`` id with a PASS observation; the rows it still accepts are
the residual docs/SECURITY.md names, and the assurance says ``blackbox_checks: unpinned``."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork import blackbox, protection
from cairnwork_attest.binding_extras import KEYS
from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.verify import verify_bundle
from cairnwork_attest.verify_binding import PolicyError, load_policy
from cairnwork_protocol import evidence_bundle_hash
from tests.attest.binding_helpers import (
    notarize_bound,
    policy,
    producer,
    receiver,
    resign,
    sign_bound,
)

_CASE = {"id": "c1", "args": [2, 3], "expected": 5}
DECLARED = blackbox.parse(
    json.dumps({"salt": "ab" * 32, "checks": [
        {"id": "mul", "module": "src.a", "function": "mul", "cases": [_CASE]},
        {"id": "add", "module": "src.a", "function": "add", "cases": [_CASE]},
    ]}).encode()
)  # fmt: skip
COMMITMENT = DECLARED.commitment()  # what the receiver recomputes from its own copy
CHECKS = sorted(check.id for check in DECLARED.checks)  # ["add", "mul"]
OTHER = "sha256:" + "cd" * 32
MISSING, MISMATCH = "BLACKBOX_CHECKS_MISSING", "BLACKBOX_COMMITMENT_MISMATCH"
PIN = {"blackbox_commitment": COMMITMENT}
STRICT = {"blackbox_commitment": COMMITMENT, "protection": "blackbox"}
ALL = ("pytest-0", "blackbox:add", "blackbox:mul")

# name: (required ids, notarize_bound kwargs, policy pins or None for no policy,
#        code with an older policy (no blackbox_checks), code with blackbox_checks added)
ROWS: dict[str, tuple[tuple[str, ...], dict[str, Any], dict[str, Any] | None, str, str]] = {
    "A_no_blackbox_ids": (("pytest-0",), {}, PIN, MISSING, MISSING),
    "B_one_check_missing": (("pytest-0", "blackbox:add"), {}, PIN, "OK", MISSING),
    "C_id_not_declared": (("pytest-0", "blackbox:zzz"), {}, PIN, "OK", MISSING),
    "D_all_present": (ALL, {}, PIN, "OK", "OK"),
    "E_no_pins": (("pytest-0",), {}, {}, "OK", "OK"),
    "F_strict_no_ids": (("pytest-0",), {}, STRICT, "UNPROTECTED", "UNPROTECTED"),
    "G_strict_one_missing": (("blackbox:add",), {}, STRICT, "OK", MISSING),
    "H_strict_foreign_id": (("blackbox:zzz",), {}, STRICT, "OK", MISSING),
    "I_required_check_failed": (ALL, {"failing": ("blackbox:mul",)}, PIN, MISSING, MISSING),
    "J_observed_not_required": (
        ("pytest-0", "blackbox:add"), {"unrequired": ("blackbox:mul",)}, PIN, "OK", MISSING
    ),
    "K_all_plus_foreign_id": ((*ALL, "blackbox:zzz"), {}, PIN, "OK", MISSING),
    "L_commitment_checked_first": (ALL, {"blackbox_commitment": OTHER}, PIN, MISMATCH, MISMATCH),
    "M_empty_pin_plain_receipt": (
        ("pytest-0",), {"blackbox_commitment": ""}, {"blackbox_commitment": ""}, "OK", "OK"
    ),
    "N_strict_all_and_gate": (
        ("blackbox:add", "blackbox:mul", "protection"), {}, STRICT, "OK", "OK"
    ),
    "O_order_differs": (("blackbox:mul", "pytest-0", "blackbox:add"), {}, PIN, "OK", "OK"),
    "P_no_policy": (("pytest-0", "blackbox:add"), {}, None, "OK", "OK"),  # out of #312's scope
}  # fmt: skip


def _world(tmp_path: Path, required: tuple[str, ...], **kw: Any) -> dict:
    p = producer(tmp_path)
    state = tmp_path / "state"
    found = protection.level(required, macos=False)  # a label Core's ids bear out
    kw.setdefault("blackbox_commitment", COMMITMENT)
    label = "" if found == protection.NONE else found
    run = notarize_bound(state, p, required=required, protection=label, **kw)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    return {**p, "state": state, "bundle": bundle, "full": full, "pub": state / "public.pem",
            "tmp": tmp_path}  # fmt: skip


def check(w: dict, pins: dict[str, Any] | None, bundle: Path | None = None):
    pol = None if pins is None else policy(w["state"], w["tmp"], w["tests"], **pins)
    return verify_bundle(
        bundle or w["bundle"], public_key_pem=w["pub"], repo=w["full"],
        expected_candidate=w["head"], policy=pol,
    )  # fmt: skip


def with_checks(pins: dict[str, Any] | None, checks: list[str] = CHECKS) -> dict[str, Any] | None:
    if pins is None or not pins.get("blackbox_commitment"):
        return pins  # blackbox_checks goes with a non-empty commitment pin only
    return pins | {"blackbox_checks": checks}


@pytest.mark.parametrize("name", sorted(ROWS))
def test_a_pinned_commitment_needs_its_declared_checks(tmp_path, name):
    required, kw, pins, older, newer = ROWS[name]
    w = _world(tmp_path, required, **kw)
    assert check(w, pins).code == older, "older policy"
    assert check(w, with_checks(pins)).code == newer, "policy with blackbox_checks"


@pytest.mark.parametrize("field", ["verifier_id", "status"])
@pytest.mark.parametrize("pins", [PIN, with_checks(PIN)], ids=["older", "with_checks"])
def test_an_observation_of_another_type_is_refused_not_raised(tmp_path, field, pins):
    """A signed observation whose id or status is a list or object (a buggy or lying signer)."""
    w = _world(tmp_path, ALL)
    stmt = json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))
    observations = stmt["predicate"]["request"]["evidence_bundle"]["observations"]
    mul = next(o for o in observations if o["verifier_id"] == "blackbox:mul")
    mul[field] = ["blackbox:mul"] if field == "verifier_id" else {"PASS": True}
    out = resign(w["state"], stmt, tmp_path / "edited.json", rehash=True)
    assert check(w, pins, out).code == MISSING


def test_the_assurance_says_whether_the_checks_were_pinned(tmp_path):
    w = _world(tmp_path, ALL)
    for pins, said in ((None, "unpinned"), (PIN, "unpinned"), (with_checks(PIN), "pinned")):
        out = check(w, pins)
        assert out.ok and out.assurance is not None, out
        assert out.assurance["blackbox_checks"] == said, pins
    out = check(w, with_checks(PIN, ["mul", "add"]))  # a set: the policy's order does not matter
    assert out.ok and out.assurance is not None and out.assurance["blackbox_checks"] == "pinned"


@pytest.mark.parametrize(
    "bad",
    [[], ["add", "add"], ["Add"], ["blackbox:add"], ["a" * 41], [""], [1], "add", None, {}],
)
def test_a_malformed_blackbox_checks_is_refused(tmp_path, bad):
    w = _world(tmp_path, ALL)
    policy(w["state"], w["tmp"], w["tests"], **PIN, blackbox_checks=bad)
    with pytest.raises(PolicyError):
        load_policy(w["tmp"] / "policy.json")
    assert check(w, PIN | {"blackbox_checks": bad}).code == "POLICY_MALFORMED"  # the API's dict


@pytest.mark.parametrize("commitment", [None, ""])
def test_blackbox_checks_needs_a_pinned_commitment(tmp_path, commitment):
    """Without a commitment the ids would not be checked at all: refuse rather than ignore."""
    w = _world(tmp_path, ALL)
    pins: dict[str, Any] = {"blackbox_checks": CHECKS}
    if commitment is not None:
        pins["blackbox_commitment"] = commitment
    policy(w["state"], w["tmp"], w["tests"], **pins)
    with pytest.raises(PolicyError):
        load_policy(w["tmp"] / "policy.json")
    assert check(w, pins).code == "POLICY_MALFORMED"


def test_a_policy_with_blackbox_checks_loads(tmp_path):
    w = _world(tmp_path, ALL)
    pinned = policy(w["state"], w["tmp"], w["tests"], **PIN, blackbox_checks=CHECKS)
    assert load_policy(w["tmp"] / "policy.json") == pinned


def test_blackbox_checks_is_a_policy_key_not_a_binding_key(tmp_path):
    """A signer cannot satisfy the pin by writing the ids into its own binding."""
    assert "blackbox_checks" not in KEYS
    w = _world(tmp_path, ("pytest-0",))
    stmt = json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))
    stmt["predicate"]["binding"]["blackbox_checks"] = CHECKS
    out = resign(w["state"], stmt, tmp_path / "edited.json")
    assert check(w, with_checks(PIN), out).code == "STATEMENT_SHAPE"


@pytest.mark.parametrize("pins", [PIN, with_checks(PIN)], ids=["older", "with_checks"])
def test_a_failed_observation_is_not_outvoted_by_a_later_pass(tmp_path, pins):
    """A required check observed FAIL and then PASS was not only PASS, even when the signer also
    re-echoes the evidence hash so nothing else refuses the receipt (ACH mutation scan)."""
    w = _world(tmp_path, ALL)
    stmt = json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))
    evidence = stmt["predicate"]["request"]["evidence_bundle"]
    observations = evidence["observations"]
    at = next(i for i, o in enumerate(observations) if o["verifier_id"] == "blackbox:mul")
    observations.insert(at, observations[at] | {"status": "FAIL", "artifact_id": "a-fail"})
    stmt["predicate"]["response"]["hashes"]["evidence_bundle_hash"] = evidence_bundle_hash(evidence)
    out = resign(w["state"], stmt, tmp_path / "edited.json", rehash=True)
    assert check(w, pins, out).code == MISSING
