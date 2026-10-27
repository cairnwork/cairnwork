"""Optional binding keys of a v0.2 receipt (#292).

The producer adds an optional key to the binding only when its value is non-empty
(notary_verify.nonempty_fields), so a receipt without it stays byte-identical and the binding hash
covers it when present. Each optional key has a shape rule for the recorded value, and a receiver
policy may pin it under the same name: an absent key counts as its empty value, and a pinned value
that differs fails with the key's own code. Adding a key means adding one ``Extra`` to ``EXTRAS``.

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
    return ""


def pin_problem(ctx: Any) -> VerifyResult | None:
    """Each optional key the policy pins must be satisfied by the binding's (equal, order included;
    for ``protection`` at least the pinned level); ctx is verify_binding's check context (policy,
    binding, request). First, the binding's protection level must be the one the signed Core
    request's required verifier ids give, so a receiver never takes the label on trust alone (only
    the ``_macos`` grade, which the ids cannot show, is the producer's word)."""
    policy, binding = ctx.policy, ctx.binding
    contract = ctx.request.get("acceptance_contract")
    ids = contract.get("required_verifier_ids") if isinstance(contract, dict) else None
    recorded = recorded_protection(binding)
    expected = protection.level(map(str, ids), macos=False) if isinstance(ids, list) else None
    if protection.coverage(recorded) != expected:  # the host grade is the producer's to state
        return _fail(
            "PROTECTION_LABEL_MISMATCH", f"protection {recorded!r} is not what Core required"
        )
    for extra in EXTRAS:
        if policy is not None and extra.key in policy:
            recorded = binding.get(extra.key, extra.empty)
            if not extra.satisfies(policy[extra.key], recorded):
                return _fail(extra.mismatch, f"the receipt records {extra.key} {recorded!r}")
    return None


def recorded_protection(binding: Mapping[str, Any]) -> str:
    return binding.get("protection", protection.NONE)
