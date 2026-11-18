"""#1353 S4 checks the signed strict gate; a signer can still lie about the run and answers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cairnwork import protection
from cairnwork_attest.verify_response import VerifyResult, _fail

KEY = "require_protection_gate"
LOW_ENTROPY = ";low-entropy"


def policy_problem(policy: Mapping[str, Any]) -> str:
    """Return why the opt-in gate policy is malformed, or an empty string."""
    if KEY not in policy:
        return ""
    required = policy[KEY]
    if not isinstance(required, bool):
        return f"policy {KEY} must be a boolean"
    if required and policy.get("protection") not in protection.REQUIRABLE:
        return f"policy {KEY} requires a protection pin in {protection.REQUIRABLE!r}"
    return ""


def gate_problem(
    policy: Mapping[str, Any] | None,
    binding: Mapping[str, Any],
    ids: list[str],
    request: Mapping[str, Any],
) -> VerifyResult | None:
    """Check signed strict-gate evidence for an opted-in v0.2 receiver policy."""
    if policy is None or policy.get(KEY) is not True:
        return None
    if ids.count(protection.GATE) != 1:
        return _fail("PROTECTION_GATE_MISSING", "Core must require protection exactly once")

    evidence = request.get("evidence_bundle")
    raw_observations = evidence.get("observations") if isinstance(evidence, dict) else None
    observations = (
        [
            obs
            for obs in raw_observations
            if isinstance(obs, dict) and obs.get("verifier_id") == protection.GATE
        ]
        if isinstance(raw_observations, list)
        else []
    )
    recorded = binding.get("protection", protection.NONE)
    if len(observations) != 1:
        return _fail("PROTECTION_GATE_MISMATCH", "Core must record one protection observation")
    obs = observations[0]
    artifact_id, artifact_hash = obs.get("artifact_id"), obs.get("artifact_hash")
    matches = [
        minimum
        for minimum in protection.LEVELS
        if protection.gate(recorded, minimum)[1:] == (artifact_id, artifact_hash)
    ]
    if obs.get("status") != "PASS" or not any(
        protection.at_least(recorded, minimum)
        and protection.at_least(recorded, policy["protection"])
        and protection.at_least(minimum, policy["protection"])
        for minimum in matches
    ):
        return _fail(
            "PROTECTION_GATE_MISMATCH",
            "gate does not match recorded level "
            f"{recorded!r} and policy {policy.get('protection')!r}",
        )

    low_entropy = (
        sorted(
            obs["verifier_id"][:80]
            for obs in raw_observations
            if isinstance(obs, dict)
            and isinstance(obs.get("verifier_id"), str)
            and obs["verifier_id"].startswith(protection.PREFIX)
            and obs["verifier_id"] in ids
            and isinstance(obs.get("artifact_id"), str)
            and obs["artifact_id"].endswith(LOW_ENTROPY)
        )
        if isinstance(raw_observations, list)
        else []
    )
    if low_entropy:
        return _fail(
            "BLACKBOX_LOW_ENTROPY",
            f"{len(low_entropy)} low-entropy checks: {low_entropy[:5]!r}",
        )
    return None
