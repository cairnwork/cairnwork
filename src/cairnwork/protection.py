"""How much of a verdict rests on protected checks (#303: council must-fix 3, James's option A).

A required observation is *protected* when it is a declared blackbox check (``blackbox:<id>``,
blackbox.py): its answers never enter the run, the owner compares the result itself, and it passes
only under the verifier account. Every other required observation, a pytest verifier or P2P, comes
from a process the candidate's code shares and could steer. Over the verifier ids Core requires:

- ``blackbox``: every one is a blackbox check;
- ``partial``: some are, and the rest are same-process;
- ``same_process_only``: none is.

A VERIFIED verdict needs every required observation passed, so its level says what that VERIFIED
rests on. Core's definition of VERIFIED is unchanged. The level is reported in cairnwork-verify's
output and recorded in a receipt binding (an absent key means ``same_process_only``).

Strict mode (``require_protection``, default off): the task names the least level it accepts, and
judge_candidate adds one more required observation, ``protection``, that passes only when the
level reaches it. An unprotected run is then not VERIFIED: Core decides, as for any observation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

PREFIX = "blackbox:"
GATE = "protection"  # the strict-mode observation's verifier id; never counted in the level
NONE, PARTIAL, FULL = "same_process_only", "partial", "blackbox"
LEVELS = (NONE, PARTIAL, FULL)
REQUIRABLE = (PARTIAL, FULL)
UNPROTECTED_CODE = "UNPROTECTED"
UNPROTECTED = f"VERIFICATION_INCOMPLETE: {UNPROTECTED_CODE}"


def reserved(verifier_id: str) -> bool:
    """A task's own verifier may not take an id the level counts or the gate uses."""
    return verifier_id.startswith(PREFIX) or verifier_id == GATE


def level(required_ids: Iterable[str]) -> str:
    ids = [verifier_id for verifier_id in required_ids if verifier_id != GATE]
    protected = sum(1 for verifier_id in ids if verifier_id.startswith(PREFIX))
    if protected and protected == len(ids):
        return FULL
    return PARTIAL if protected else NONE


def at_least(found: str, minimum: str) -> bool:
    """Does level ``found`` meet ``minimum``? An unknown value never does."""
    if found not in LEVELS or minimum not in LEVELS:
        return False
    return LEVELS.index(found) >= LEVELS.index(minimum)


def gate(found: str, minimum: str) -> tuple[bool, str, str]:
    """Strict mode's observation: (passed, artifact id, artifact hash) for level ``found``."""
    record = json.dumps({"level": found, "minimum": minimum}, sort_keys=True).encode()
    digest = "sha256:" + hashlib.sha256(record).hexdigest()
    return at_least(found, minimum), f"protection#{found}>={minimum}", digest


def reason(found: str, minimum: str) -> str:
    if at_least(found, minimum):
        return f"{GATE}: PASS [{found}]"
    return f"{GATE}: {UNPROTECTED} [{found}; required {minimum}]"
