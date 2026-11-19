"""Injected local-first task routing with acceptance and gate checks at every stage."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Protocol

from experimental.local_relay.config import FLAG, RelayConfig
from experimental.local_relay.ledger import Ledger
from experimental.local_worker.endpoint import (
    EndpointContextLimit,
    EndpointTimeout,
    EndpointUnreachable,
)
from experimental.pause.interfaces import CallSettings, Gate, GateReply, GateRequest
from experimental.pause.schema import GATE_VERDICTS


@dataclass(frozen=True)
class Acceptance:
    passed: bool
    raw: str


@dataclass(frozen=True)
class Attempt:
    candidate: Any
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


@dataclass(frozen=True)
class RelayResult:
    route: str
    candidate: Any = None
    acceptance_passed: bool | None = None
    acceptance_raw: str | None = None
    gate_verdict: str | None = None
    reason: str | None = None


class Worker(Protocol):
    def __call__(self, context_tokens: int, /) -> Attempt: ...


class AcceptanceCheck(Protocol):
    def __call__(self, candidate: Any, /) -> Acceptance: ...


class Fallback(Protocol):
    def __call__(self, task_id: str, /) -> Attempt: ...


def run_relay(
    task_id: str,
    worker: Worker,
    acceptance: AcceptanceCheck,
    gate: Gate,
    fallback: Fallback,
    ledger: Ledger,
    config: RelayConfig | None = None,
) -> RelayResult:
    """Route a task; every stage succeeds only when acceptance and gate both pass."""
    config = config or RelayConfig.from_env()
    # The environment flag is always required; a config object cannot bypass it.
    if not (config.enabled and os.environ.get(FLAG) == "1"):
        return RelayResult("DISABLED", reason="RELAY_DISABLED")
    try:
        for stage, context in (
            ("local", config.default_context_tokens),
            ("retry", config.retry_context_tokens),
        ):
            attempt = _local_attempt(task_id, worker, acceptance, context, ledger, stage)
            if attempt is None:
                continue
            checked = _check_stage(task_id, attempt, acceptance, gate, ledger, config, stage)
            if checked.route == "FAILED":
                return checked
            if checked.route == "ACCEPTED":
                return replace(checked, route="LOCAL_FIRST" if stage == "local" else "LOCAL_RETRY")
        return _fallback_stage(task_id, fallback, acceptance, gate, ledger, config)
    except LedgerError as exc:
        return RelayResult("LEDGER_ERROR", reason=str(exc))


def _local_attempt(task_id, worker, acceptance, context, ledger, stage):
    try:
        attempt = worker(context)
    except (EndpointContextLimit, EndpointTimeout, EndpointUnreachable):
        _append(ledger, _row(task_id, stage, "local", 0, 0, 0.0))
        return None
    _append(
        ledger,
        _row(task_id, stage, attempt.model_id, attempt.input_tokens, attempt.output_tokens, 0.0),
    )
    return attempt


def _check_stage(task_id, attempt, acceptance, gate, ledger, config, stage):
    result = acceptance(attempt.candidate)
    settings = CallSettings(config.gate_model_id, config.gate_effort, config.timeout_seconds, 1)
    try:
        reply = gate.review(GateRequest(task_id, "", str(attempt.candidate), settings))
    except Exception as exc:  # noqa: BLE001 - injected gate failures must fail closed.
        return RelayResult("FAILED", attempt.candidate, result.passed, result.raw, reason=str(exc))
    _append(
        ledger,
        _row(
            task_id,
            stage,
            reply.model_id,
            getattr(reply, "input_tokens", 0),
            getattr(reply, "output_tokens", 0),
            _cost(reply),
        ),
    )
    reason = _verdict_reason(reply)
    if result.passed and reply.verdict == "ACCEPT" and reply.reason is None:
        return RelayResult("ACCEPTED", attempt.candidate, result.passed, result.raw, reply.verdict)
    return RelayResult(
        "REJECTED", attempt.candidate, result.passed, result.raw, reply.verdict, reason
    )


def _fallback_stage(task_id, fallback, acceptance, gate, ledger, config):
    try:
        attempt = fallback(task_id)
    except Exception as exc:  # noqa: BLE001 - injected fallback failures are terminal.
        return RelayResult("FAILED", reason=str(exc))
    _append(
        ledger,
        _row(
            task_id,
            "fallback",
            attempt.model_id,
            attempt.input_tokens,
            attempt.output_tokens,
            attempt.usd,
        ),
    )
    checked = _check_stage(task_id, attempt, acceptance, gate, ledger, config, "fallback")
    if checked.route != "ACCEPTED":
        return replace(checked, route="FAILED")
    return replace(checked, route="FALLBACK")


def _verdict_reason(reply: GateReply) -> str | None:
    if reply.reason is not None:
        return str(reply.reason)
    if reply.verdict not in GATE_VERDICTS:
        return "GATE_VERDICT_UNKNOWN"
    return None if reply.verdict == "ACCEPT" else "GATE_REJECTED"


def _cost(reply: GateReply) -> float:
    return float(getattr(reply, "usd", 0.0))


def _row(task_id, stage, model_id, input_tokens, output_tokens, usd):
    return {
        "task_id": task_id,
        "stage": stage,
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
    }


def _append(ledger, row):
    try:
        ledger.append(row)
    except Exception as exc:
        raise LedgerError(str(exc)) from exc


class LedgerError(RuntimeError):
    """Ledger failure; callers must stop before another cloud call."""
