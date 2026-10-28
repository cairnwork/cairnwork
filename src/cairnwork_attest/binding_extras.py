"""Optional binding keys of a v0.2 receipt (#292).

The producer adds an optional key to the binding only when its value is non-empty
(notary_verify.nonempty_fields), so a receipt without it stays byte-identical and the binding hash
covers it when present. Each optional key has a shape rule for the recorded value, and a receiver
policy may pin it under the same name: an absent key counts as its empty value, and a pinned value
that differs fails with the key's own code. Adding a key means adding one ``Extra`` to ``EXTRAS``.
``blackbox_checks`` (#312) is a policy key only, never a binding key, so it is not one of them.

Only the shape is checked here, not the producer's content rules (issue_task.check_import_paths):
the value is signed, so a stricter receiver-side rule would only make a valid old receipt fail
on a newer receiver when those rules change."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from cairnwork import protection
from cairnwork_attest.verify_response import VerifyResult, _fail


def _equal(pinned: Any, recorded: Any) -> bool:
    return pinned == recorded


@dataclass(frozen=True)
class Extra:
    key: str
    empty: Any  # what an absent key stands for
    shape: Callable[[Any], bool]  # True when a (possibly empty) value has the right shape
    mismatch: str  # the failure code when the recorded value does not satisfy the policy's
    satisfies: Callable[[Any, Any], bool] = _equal  # (policy value, recorded value)


def _path_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 8
        and all(isinstance(p, str) and p and not p.startswith("/") for p in value)
        and len(set(value)) == len(value)
    )


def _commitment(value: Any) -> bool:
    """#303: the salted hash of a blackbox declaration; a receiver pins what it recomputes from the
    declaration file (``cairnwork-verify blackbox-commitment``)."""
    return isinstance(value, str) and (value == "" or bool(_SHA256.fullmatch(value)))


def _level(value: Any) -> bool:
    """#303: what the verdict rests on (cairnwork/protection.py). In a policy it is the least
    level the receiver accepts (strict mode on the receiver's side), not a value to equal."""
    return isinstance(value, str) and value in protection.LEVELS


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_CHECK_ID = re.compile(r"[a-z0-9_-]{1,40}")  # blackbox.py's check id
EXTRAS = (
    Extra("import_paths", [], _path_list, "IMPORT_PATHS_MISMATCH"),
    Extra("blackbox_commitment", "", _commitment, "BLACKBOX_COMMITMENT_MISMATCH"),
    Extra(
        "protection",
        protection.NONE,
        _level,
        protection.UNPROTECTED_CODE,
        lambda minimum, recorded: protection.at_least(recorded, minimum),
    ),  # fmt: skip
)
KEYS = frozenset(extra.key for extra in EXTRAS)


def binding_problem(binding: Mapping[str, Any]) -> str:
    """Why an optional key in the binding is malformed (empty values are never recorded)."""
    for extra in EXTRAS:
        if extra.key in binding:
            value = binding[extra.key]
            if not extra.shape(value) or value == extra.empty:
                return f"binding {extra.key} has the wrong shape or is empty"
    return ""


def policy_problem(policy: Mapping[str, Any]) -> str:
    """Why a pinned optional key in the policy is malformed (an empty pin is allowed)."""
    for extra in EXTRAS:
        if extra.key in policy and not extra.shape(policy[extra.key]):
            return f"policy {extra.key} has the wrong shape"
    if "blackbox_checks" in policy and not (  # #312: only ever checked with a non-empty pin
        _check_ids(policy["blackbox_checks"]) and policy.get("blackbox_commitment")
    ):
        return "policy blackbox_checks: distinct check ids, with a non-empty blackbox_commitment"
    return ""


def _check_ids(value: Any) -> bool:
    """The declaration's check ids (blackbox.py), as ``blackbox-commitment`` prints them."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(i, str) and _CHECK_ID.fullmatch(i) for i in value)
        and len(set(value)) == len(value)
    )


def pin_problem(ctx: Any) -> VerifyResult | None:
    """Each optional key the policy pins must be satisfied by the binding's (equal, order included;
    for ``protection`` at least the pinned level); ctx is verify_binding's check context (policy,
    binding, request). First, the binding's protection level must be the one the signed Core
    request's required verifier ids give (only the ``_macos`` grade, which the ids cannot show, is
    the producer's word), and a level above ``same_process_only`` needs a blackbox commitment in
    the binding (PROTECTION_UNCOMMITTED): the ``blackbox:*`` ids then stand for a declaration the
    receiver can pin, never for the signer's naming alone."""
    policy, binding = ctx.policy, ctx.binding
    contract = ctx.request.get("acceptance_contract")
    ids = contract.get("required_verifier_ids") if isinstance(contract, dict) else None
    recorded = recorded_protection(binding)
    expected = protection.level(map(str, ids), macos=False) if isinstance(ids, list) else None
    if protection.coverage(recorded) != expected:  # the host grade is the producer's to state
        return _fail(
            "PROTECTION_LABEL_MISMATCH", f"protection {recorded!r} is not what Core required"
        )
    if recorded != protection.NONE and not binding.get("blackbox_commitment"):
        return _fail("PROTECTION_UNCOMMITTED", f"protection {recorded!r} without a declaration")
    for extra in EXTRAS:
        if policy is not None and extra.key in policy:
            recorded = binding.get(extra.key, extra.empty)
            if not extra.satisfies(policy[extra.key], recorded):
                return _fail(extra.mismatch, f"the receipt records {extra.key} {recorded!r}")
    if policy is None or not policy.get("blackbox_commitment"):
        return None
    return _checks_problem(policy, ids or [], ctx.request)  # a list: the label check passed


def _checks_problem(
    policy: Mapping[str, Any], ids: list[Any], request: Mapping[str, Any]
) -> VerifyResult | None:
    """#312: a pinned commitment names the declaration, not which of its checks Core required.
    With ``blackbox_checks`` the request's ``blackbox:*`` ids must be exactly those; an older
    policy without it only asks for one (assurance ``blackbox_checks: unpinned``, the residual in
    docs/SECURITY.md). Each must be observed in the evidence, and only as PASS."""
    got = {i for i in ids if isinstance(i, str) and i.startswith(protection.PREFIX)}
    want = {protection.PREFIX + c for c in policy.get("blackbox_checks", [])}
    evidence = request.get("evidence_bundle")
    observations = evidence.get("observations") if isinstance(evidence, dict) else None
    status: dict[Any, set[Any]] = {}
    for obs in observations if isinstance(observations, list) else []:
        if isinstance(obs, dict):
            status.setdefault(obs.get("verifier_id"), set()).add(obs.get("status"))
    unpassed = sorted(i for i in got if status.get(i) != {"PASS"})
    if (got != want if "blackbox_checks" in policy else not got) or unpassed:
        detail = f"Core required {sorted(got)}, the policy {sorted(want) or 'one or more'}"
        return _fail("BLACKBOX_CHECKS_MISSING", f"{detail}; not PASS: {unpassed}")
    return None


def recorded_protection(binding: Mapping[str, Any]) -> str:
    return binding.get("protection", protection.NONE)
