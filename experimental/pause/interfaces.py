"""The interfaces later PAUSE steps implement (#476); types only, no logic here.

- ``Planner`` and ``Gate``: step 2 (the Haiku clients; ``cli gate-replay`` drives a ``Gate``).
- ``CheckoutGuard`` and ``EventLog``: step 3b (AC8's file-level guard and its single controller).
- Step 3 wires them around local_worker's resume; step 4 injects failures through fakes of them
  (tests/pause_fakes.py).

A client never raises for an expected failure: it returns a reply whose ``reason`` is the code
(schema.Reason) and whose text or verdict is None. It always reports the model id and effort it
actually sent, and never switches model or retries beyond ``CallSettings.max_attempts``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from experimental.pause.schema import Reason


@dataclass(frozen=True)
class CallSettings:
    """Frozen per run (FREEZE): the model, the effort sent explicitly on every call, the limits."""

    model_id: str
    effort: str
    timeout_seconds: float
    max_attempts: int


@dataclass(frozen=True)
class PlanRequest:
    task_id: str
    checkpoint_sha256: str
    messages: tuple[dict, ...]  # the resume prefix: system prompt, task, turns before the cut
    settings: CallSettings


@dataclass(frozen=True)
class PlanReply:
    text: str | None  # None exactly when reason is set
    model_id: str
    effort: str
    reason: Reason | None = None


@dataclass(frozen=True)
class GateRequest:
    task_id: str
    checkpoint_sha256: str
    plan: str
    settings: CallSettings


@dataclass(frozen=True)
class GateReply:
    verdict: str | None  # schema.GATE_VERDICTS; None exactly when reason is set
    model_id: str
    effort: str
    reason: Reason | None = None


@runtime_checkable
class Planner(Protocol):
    def plan(self, request: PlanRequest) -> PlanReply: ...


@runtime_checkable
class Gate(Protocol):
    def review(self, request: GateRequest) -> GateReply: ...


class GuardUnavailable(Exception):
    """The guard cannot be applied: the run stops and falls back to the bare run (fail closed)."""

    reason = Reason.PAUSE_GUARD_UNAVAILABLE


@runtime_checkable
class CheckoutGuard(Protocol):
    """AC8 (b): a digest of the checkout (tracked content, untracked and ignored files, file
    modes) before and after every tool call until the plan arrives; any change is rolled back."""

    def digest(self) -> str: ...  # raises GuardUnavailable

    def rollback(self, to: str) -> None: ...  # back to the state of digest ``to``, or raises


@runtime_checkable
class EventLog(Protocol):
    """AC8's single controller: every event gets the next number of one monotonic sequence."""

    def record(self, kind: str, **fields: Any) -> int: ...  # the event's sequence number
