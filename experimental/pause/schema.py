"""The one place PAUSE's reason codes and receipt field names are defined (#476, amendment 1
point 3). Every other module, the tests and layer2's PREREG read them from here; none copies the
strings."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum

RECEIPT_SCHEMA = "cairnwork.pause.receipt/v1"
RECEIPTS_FILE = "pause-receipts.jsonl"  # one JSON line per receipt; only with the flag on


class Reason(StrEnum):
    """Why PAUSE did not deliver a plan (every code but the trigger), or what triggered it.

    ``*_REFUSED``: the API refused (401, 429, 5xx). ``*_REFUSAL``: the model declined (HTTP 200,
    stop_reason refusal); the client never resends to another model. ``*_QUOTA_EXHAUSTED``: the
    call wrapper's prepaid budget or cap stopped the call before it was sent."""

    # AC3 as written in #476
    PAUSE_PLANNER_TIMEOUT = "PAUSE_PLANNER_TIMEOUT"
    PAUSE_PLANNER_REFUSED = "PAUSE_PLANNER_REFUSED"
    PAUSE_PLANNER_REFUSAL = "PAUSE_PLANNER_REFUSAL"
    PAUSE_GATE_BAD_OUTPUT = "PAUSE_GATE_BAD_OUTPUT"
    PAUSE_GATE_REFUSAL = "PAUSE_GATE_REFUSAL"
    PAUSE_LOCAL_UNREACHABLE = "PAUSE_LOCAL_UNREACHABLE"
    PAUSE_WORKER_CRASH = "PAUSE_WORKER_CRASH"
    # AC2: the cut is UNOBSERVED; AC4: the outgoing request matched the secret scan
    PAUSE_NO_CUT = "PAUSE_NO_CUT"
    PAUSE_SECRET_BLOCKED = "PAUSE_SECRET_BLOCKED"
    # amendment 2, the five fallback classes: M5 out of memory when the endpoint or the lab
    # memory guard says so (otherwise LOCAL_UNREACHABLE); planner quota; the gate's REJECT
    # count reached its limit (a single REJECT with a limit of 1 included)
    PAUSE_LOCAL_OUT_OF_MEMORY = "PAUSE_LOCAL_OUT_OF_MEMORY"
    PAUSE_PLANNER_QUOTA_EXHAUSTED = "PAUSE_PLANNER_QUOTA_EXHAUSTED"
    PAUSE_GATE_REJECT_LIMIT = "PAUSE_GATE_REJECT_LIMIT"
    # the gate's API side, as the planner's (step 2's client needs them)
    PAUSE_GATE_TIMEOUT = "PAUSE_GATE_TIMEOUT"
    PAUSE_GATE_REFUSED = "PAUSE_GATE_REFUSED"
    PAUSE_GATE_QUOTA_EXHAUSTED = "PAUSE_GATE_QUOTA_EXHAUSTED"
    # step 3: the resumed run's final patch has a path outside write_scope, or a non-regular
    # file (symlink 120000, gitlink 160000); the resumed result is discarded
    PAUSE_SCOPE_VIOLATION = "PAUSE_SCOPE_VIOLATION"
    # AC8: a checkout change before the plan arrived was rolled back and triggered PAUSE (the
    # only code an intervention may carry); the guard could not be applied (fail closed)
    PAUSE_GUARD_ROLLED_BACK = "PAUSE_GUARD_ROLLED_BACK"
    PAUSE_GUARD_UNAVAILABLE = "PAUSE_GUARD_UNAVAILABLE"


TRIGGER_REASONS = (Reason.PAUSE_GUARD_ROLLED_BACK,)
FALLBACK_REASONS = tuple(r for r in Reason if r not in TRIGGER_REASONS)  # intervened: false
GATE_VERDICTS = ("ACCEPT", "REJECT")


@dataclass(frozen=True)
class ReceiptFields:
    """The receipt's fields in line order (receipt.build fills them; receipt_sha256 follows).

    checkpoint_sha256: sha256 of the parent transcript file the plan was written for; plan_sha256:
    sha256 of the plan text as UTF-8 (local_worker's own plan_sha256); *_effort: the effort value
    actually sent; event_seq: the event controller's sequence number of this decision (AC8);
    guard_rollbacks: checkout changes rolled back before the plan arrived."""

    schema: str
    task_id: str
    episode_id: str | None
    checkpoint_sha256: str | None
    cut_turn_index: int | None
    plan_sha256: str | None
    planner_model_id: str | None
    planner_effort: str | None
    gate_model_id: str | None
    gate_effort: str | None
    gate_verdict: str | None
    reason: str | None
    intervened: bool
    guard_rollbacks: int
    event_seq: int


RECEIPT_FIELDS = (*(f.name for f in fields(ReceiptFields)), "receipt_sha256")
AC2_FIELDS = ("task_id", "checkpoint_sha256", "plan_sha256", "cut_turn_index")
RECEIPT_VOLATILE_FIELDS = ("episode_id", "receipt_sha256")  # differ between replays of one run

# #476 Open 2: what AC1 drops from local_worker's episode line, at any depth, before comparing
# the bare run with PAUSE's flag-off run: run identities and wall-clock readings. Every other
# field must be equal, the outcomes derived from the clock included (cut_900s's exit_reason,
# turns_completed, tree_differs_at_limit; Codex review of #530).
EPISODE_VOLATILE_FIELDS = (
    "episode_id",
    "checkout_id",
    "wall_clock_start",
    "wall_clock_end",
    "wall_clock_seconds",
    "own_wall_seconds",
    "t_start_seconds",
    "t_end_seconds",
    "duration_ms",
    "pre_cut_wall_seconds",
    "pre_wall_seconds",
)
