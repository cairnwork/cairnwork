"""How much of a verdict rests on protected checks (#303: council must-fix 3, James's option A).

A required observation is *protected* when it is a declared blackbox check (``blackbox:<id>``,
blackbox.py): its answers never enter the run, the owner compares the result itself, and it passes
only under the verifier account. Every other required observation, a pytest verifier or P2P, comes
from a process the candidate's code shares and could steer. Over the verifier ids Core requires:

- ``blackbox``: every one is a blackbox check;
- ``partial``: some are, and the rest are same-process;
- ``same_process_only``: none is.

On macOS the two protected levels carry a suffix, ``blackbox_macos`` and ``partial_macos``: the
blackbox ran under Seatbelt, where whether a named IPC object exists still crosses from one verify
to a later one on the same host (#316, docs/SECURITY.md), so they are a lower grade than the same
level under Linux bwrap. A minimum without the suffix accepts only the Linux grade; one with it
accepts either.

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
import sys
from collections.abc import Iterable

from cairnwork.p2p_selection import VERIFIER_IDS as P2P_IDS

PREFIX = "blackbox:"
GATE = "protection"  # the strict-mode observation's verifier id; never counted in the level
NONE, PARTIAL, FULL = "same_process_only", "partial", "blackbox"
MACOS = "_macos"  # protected, with the macOS cross-run IPC residual (#316)
COVERAGE = (NONE, PARTIAL, FULL)
REQUIRABLE = (PARTIAL + MACOS, PARTIAL, FULL + MACOS, FULL)
LEVELS = (NONE, *REQUIRABLE)
WEAKEST = PARTIAL + MACOS  # cairnwork-verify --require-protection: protected, on any host
UNPROTECTED_CODE = "UNPROTECTED"
UNPROTECTED = f"VERIFICATION_INCOMPLETE: {UNPROTECTED_CODE}"
RESERVED = ", ".join((f"{PREFIX}*", GATE, *P2P_IDS))  # for the refusal (pipeline.check_task)


def reserved(verifier_id: str) -> bool:
    """A task's own verifier may not take an id the level counts, the gate uses or P2P adds (#644:
    refused before binding, not after the worker ran, p2p.py's P2P_VERIFIER_ID_CLASH)."""
    return verifier_id.startswith(PREFIX) or verifier_id in (GATE, *P2P_IDS)


def on_macos() -> bool:
    """The blackbox runs under Seatbelt here (the only macOS backend)."""
    return sys.platform == "darwin"


def level(required_ids: Iterable[str], macos: bool | None = None) -> str:
    """The level of these ids, on this host unless ``macos`` says which."""
    ids = [verifier_id for verifier_id in required_ids if verifier_id != GATE]
    protected = sum(1 for verifier_id in ids if verifier_id.startswith(PREFIX))
    found = FULL if protected and protected == len(ids) else PARTIAL if protected else NONE
    macos = on_macos() if macos is None else macos
    return found + MACOS if macos and found != NONE else found


def coverage(found: str) -> str:
    """The level without the host grade: what the ids alone say."""
    return found.removesuffix(MACOS) if found in LEVELS else ""


def at_least(found: str, minimum: str) -> bool:
    """Does level ``found`` meet ``minimum``? As many protected ids, and the Linux grade unless the
    minimum carries the suffix. An unknown value never does."""
    if found not in LEVELS or minimum not in LEVELS:
        return False
    graded = minimum == NONE or minimum.endswith(MACOS) or not found.endswith(MACOS)
    return graded and COVERAGE.index(coverage(found)) >= COVERAGE.index(coverage(minimum))


def gate(found: str, minimum: str) -> tuple[bool, str, str]:
    """Strict mode's observation: (passed, artifact id, artifact hash) for level ``found``."""
    record = json.dumps({"level": found, "minimum": minimum}, sort_keys=True).encode()
    digest = "sha256:" + hashlib.sha256(record).hexdigest()
    return at_least(found, minimum), f"protection#{found}>={minimum}", digest


def reason(found: str, minimum: str) -> str:
    if at_least(found, minimum):
        return f"{GATE}: PASS [{found}]"
    return f"{GATE}: {UNPROTECTED} [{found}; required {minimum}]"
