"""Receiver policy rules for #1353 Stage 4 strict protection evidence."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork import protection
from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.verify_binding import PolicyError, load_policy
from tests.attest.binding_helpers import (
    notarize_bound,
    policy,
    producer,
    receiver,
    resign,
    sign_bound,
)
from tests.attest.test_binding_protection import COMMITMENT, check
from tests.attest.test_binding_protection import _world as loose_world

STRICT_IDS = ("pytest-0", "blackbox:add", protection.GATE)
GATE_ARTIFACT = protection.gate("partial", protection.WEAKEST)[1:]


def _strict_world(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = producer(tmp_path)
    state = tmp_path / "state"
    artifacts = {protection.GATE: GATE_ARTIFACT}
    artifacts.update(overrides.pop("artifacts", {}))
    run = notarize_bound(
        state,
        p,
        protection="partial",
        required=STRICT_IDS,
        blackbox_commitment=COMMITMENT,
        artifacts=artifacts,
        **overrides,
    )
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    return {**p, "state": state, "bundle": bundle, "full": full, "pub": state / "public.pem",
            "tmp": tmp_path}  # fmt: skip


def _gate_policy(w: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"protection": "partial_macos", "require_protection_gate": True}
    values.update(overrides)
    return policy(w["state"], w["tmp"], w["tests"], **values)


def test_opt_in_gate_requires_a_strict_signed_request(tmp_path):
    strict = _strict_world(tmp_path / "strict")
    assert check(strict, policy=_gate_policy(strict)).ok

    loose = loose_world(tmp_path / "loose", "partial", ("pytest-0", "blackbox:add"))
    assert check(loose, policy=_gate_policy(loose)).code == "PROTECTION_GATE_MISSING"
    assert check(
        loose,
        policy=policy(loose["state"], loose["tmp"], loose["tests"], protection="partial_macos"),
    ).ok


@pytest.mark.parametrize(
    "kwargs",
    [
        {"failing": (protection.GATE,)},
        {"unrequired": (protection.GATE,)},
        {"artifacts": {protection.GATE: protection.gate("blackbox", protection.WEAKEST)[1:]}},
        {"artifacts": {protection.GATE: (GATE_ARTIFACT[0], "sha256:" + "e" * 64)}},
        {"artifacts": {protection.GATE: protection.gate("partial", protection.NONE)[1:]}},
    ],
    ids=["fail", "duplicate-observation", "other-level", "wrong-hash", "weak-minimum"],
)
def test_gate_observation_must_match_the_policy(kwargs, tmp_path):
    # Duplicate observations must reach the gate rule: the added observation is PASS, so
    # existing unrequired-observation handling does not refuse it first.
    w = _strict_world(tmp_path / "world", **kwargs)
    assert check(w, policy=_gate_policy(w)).code == "PROTECTION_GATE_MISMATCH"


def test_policy_level_must_fit_the_fixed_producer_minimum(tmp_path):
    w = _strict_world(tmp_path)
    pol = _gate_policy(w, protection="partial")
    assert check(w, policy=pol).code == "PROTECTION_GATE_MISMATCH"


def test_low_entropy_blackbox_is_refused_only_when_opted_in(tmp_path):
    artifacts = {
        protection.GATE: GATE_ARTIFACT,
        "blackbox:add": ("blackbox#add;low-entropy", "sha256:" + "a" * 64),
    }
    w = _strict_world(tmp_path / "world", artifacts=artifacts)
    assert check(w, policy=_gate_policy(w)).code == "BLACKBOX_LOW_ENTROPY"
    plain = policy(w["state"], w["tmp"], w["tests"], protection="partial_macos")
    assert check(w, policy=plain).ok


@pytest.mark.parametrize(
    "extra",
    [
        {"require_protection_gate": True},
        {"require_protection_gate": True, "protection": "same_process_only"},
        {"require_protection_gate": "yes"},
        {"require_protection_gate": 1},
        {"require_protection_gate": None},
    ],
    ids=["missing-pin", "unprotected-pin", "string", "integer", "null"],
)
def test_malformed_gate_policy_is_rejected_by_both_entry_points(tmp_path, extra):
    w = _strict_world(tmp_path)
    value = policy(w["state"], w["tmp"], w["tests"], **extra)
    with pytest.raises(PolicyError) as exc:
        load_policy(w["tmp"] / "policy.json")
    assert exc.value.code == "POLICY_MALFORMED"
    assert check(w, policy=value).code == "POLICY_MALFORMED"


def test_gate_false_is_valid_and_off(tmp_path):
    w = _strict_world(tmp_path)
    value = policy(
        w["state"], w["tmp"], w["tests"], protection="partial", require_protection_gate=False
    )
    assert load_policy(w["tmp"] / "policy.json")["require_protection_gate"] is False
    assert check(w, policy=value).ok


def test_protection_pin_refusal_still_precedes_gate_rules(tmp_path):
    w = _strict_world(tmp_path)
    value = _gate_policy(w, protection="blackbox")
    assert check(w, policy=value).code == "UNPROTECTED"


def test_transplanted_gate_request_is_refused_by_core_response_binding(tmp_path):
    w = _strict_world(tmp_path)
    statement = json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))
    observations = statement["predicate"]["request"]["evidence_bundle"]["observations"]
    next(obs for obs in observations if obs["verifier_id"] == protection.GATE)["artifact_id"] = (
        "forged"
    )
    edited = resign(w["state"], statement, tmp_path / "transplanted.json", rehash=True)
    out = check(w, edited, policy=_gate_policy(w))
    # The receiver's new gate check sees the transplanted artifact before Core response hashes.
    assert not out.ok
    assert out.code == "PROTECTION_GATE_MISMATCH"
