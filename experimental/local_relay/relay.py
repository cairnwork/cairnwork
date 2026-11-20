"""Injected local-first task routing with acceptance and gate checks at every stage."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Protocol

from experimental.local_relay.config import ARM_HAIKU_DIRECT, FLAG, RelayConfig
from experimental.local_relay.diagnostics import redact
from experimental.local_relay.gate_plan import PlanIncomplete
from experimental.local_relay.ledger import Ledger
from experimental.local_worker.endpoint import (
    EndpointContextLimit,
    EndpointTimeout,
    EndpointUnreachable,
)
from experimental.pause.interfaces import CallSettings, Gate, GateReply, GateRequest
from experimental.pause.schema import GATE_VERDICTS, Reason
from experimental.pause.secrets_scan import Scanner


@dataclass(frozen=True)
class Acceptance:
    passed: bool
    raw: str
    detail: Any = None  # why Core refused (diagnostics only)


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
    stages: tuple = ()  # per-stage evidence: worker failures, acceptance and gate outcomes


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
    trail: list[dict] = []
    try:
        return replace(
            _route(task_id, worker, acceptance, gate, fallback, ledger, config, trail),
            stages=tuple(trail),
        )
    except LedgerError as exc:
        return RelayResult("LEDGER_ERROR", reason=str(exc), stages=tuple(trail))


def _route(task_id, worker, acceptance, gate, fallback, ledger, config, trail):
    if config.arm == ARM_HAIKU_DIRECT:  # A0: no local stages; same acceptance, same gate
        direct = _fallback_stage(task_id, fallback, acceptance, gate, ledger, config, (), trail)
        return replace(direct, route="HAIKU_DIRECT") if direct.route == "FALLBACK" else direct
    if True:
        failures = []
        for stage, context in (
            ("local", config.default_context_tokens),
            ("retry", config.retry_context_tokens),
        ):
            attempt, failure = _local_attempt(task_id, worker, context, ledger, stage)
            if failure:
                failures.append(f"{stage}: {failure}")
                trail.append({"stage": stage, "worker_failure": failure})
            if attempt is None:
                continue
            checked = _check_stage(task_id, attempt, acceptance, gate, ledger, config, stage, trail)
            if checked.route == "FAILED":
                return checked
            if checked.route == "ACCEPTED":
                return replace(checked, route="LOCAL_FIRST" if stage == "local" else "LOCAL_RETRY")
        return _fallback_stage(task_id, fallback, acceptance, gate, ledger, config, failures, trail)


def _local_attempt(task_id, worker, context, ledger, stage):
    try:
        attempt = worker(context)
    except (EndpointContextLimit, EndpointTimeout, EndpointUnreachable, LocalAttemptFailed) as exc:
        failure = str(exc)
        _append(ledger, _row(task_id, stage, "local", 0, 0, 0.0, failure))
        return None, failure
    _append(
        ledger,
        _row(task_id, stage, attempt.model_id, attempt.input_tokens, attempt.output_tokens, 0.0),
    )
    return attempt, None


class LocalAttemptFailed(RuntimeError):
    """A local episode ended without a completed candidate; retry or fall back safely.

    ``usd``/``input_tokens``/``output_tokens`` are what a metered (cloud) attempt spent before it
    failed; None means unknown, which the ledger records as unknown, never as zero.
    """

    usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _check_stage(task_id, attempt, acceptance, gate, ledger, config, stage, trail):
    result = acceptance(attempt.candidate)
    settings = CallSettings(config.gate_model_id, config.gate_effort, config.timeout_seconds, 1)
    mark_stage = getattr(gate, "set_stage", None)
    if callable(mark_stage):
        mark_stage(stage)  # lets a metered gate label its spend per stage
    try:
        plan = config.plan_for(attempt.candidate) if config.plan_for else str(attempt.candidate)
    except PlanIncomplete as exc:  # part of the change cannot be shown: never approve it
        trail.append(_stage(stage, result, None, f"PLAN_INCOMPLETE: {exc}"))
        return RelayResult(
            "REJECTED", attempt.candidate, result.passed, result.raw, None, "PLAN_INCOMPLETE"
        )
    except Exception as exc:  # noqa: BLE001 - plan failures fail closed like gate failures.
        trail.append(_stage(stage, result, None, f"plan failed: {exc}"))
        return RelayResult("FAILED", attempt.candidate, result.passed, result.raw, reason=str(exc))
    masked_count, masked_categories = 0, []
    if config.mask_secret_lines:
        plan, masked_count, masked_categories = _mask_secret_lines(plan)
        if plan is None:
            entry = _stage(stage, result, None, Reason.PAUSE_SECRET_BLOCKED)
            entry.update(masked_line_count=masked_count, masked_categories=masked_categories)
            entry["gate_called"] = False
            trail.append(entry)
            _append(
                ledger,
                _row(
                    task_id,
                    stage,
                    config.gate_model_id,
                    0,
                    0,
                    0.0,
                    extra={
                        "gate_reason": str(Reason.PAUSE_SECRET_BLOCKED),
                        "masked_line_count": masked_count,
                        "masked_categories": masked_categories,
                    },
                ),
            )
            return RelayResult(
                "REJECTED",
                attempt.candidate,
                result.passed,
                result.raw,
                None,
                Reason.PAUSE_SECRET_BLOCKED,
            )
    try:
        reply = gate.review(GateRequest(task_id, "", plan, settings))
    except Exception as exc:  # noqa: BLE001 - injected gate failures must fail closed.
        trail.append(_stage(stage, result, None, f"gate raised: {exc}"))
        return RelayResult("FAILED", attempt.candidate, result.passed, result.raw, reason=str(exc))
    reason = _verdict_reason(reply)
    entry = _stage(stage, result, reply, reason)
    if config.mask_secret_lines:
        entry.update(masked_line_count=masked_count, masked_categories=masked_categories)
    trail.append(entry)
    _append(
        ledger,
        _row(
            task_id,
            stage,
            reply.model_id,
            getattr(reply, "input_tokens", 0),
            getattr(reply, "output_tokens", 0),
            _cost(reply),
            extra={k: v for k, v in entry.items() if k != "stage"},
        ),
    )
    if result.passed and reply.verdict == "ACCEPT" and reply.reason is None:
        return RelayResult("ACCEPTED", attempt.candidate, result.passed, result.raw, reply.verdict)
    return RelayResult(
        "REJECTED", attempt.candidate, result.passed, result.raw, reply.verdict, reason
    )


def _mask_secret_lines(text: str) -> tuple[str | None, int, list[str]]:
    """Mask scanner-matching lines and fail closed if the whole result still matches."""
    scanner = Scanner()
    masked, categories = [], set()
    count = 0
    for line in text.splitlines(keepends=True):
        ending = (
            "\r\n"
            if line.endswith("\r\n")
            else "\n"
            if line.endswith("\n")
            else "\r"
            if line.endswith("\r")
            else ""
        )
        hits = scanner.scan(line)
        if hits:
            count += 1
            categories.update(hits)
            masked.append(f"[line masked: {', '.join(sorted(set(hits)))}]" + ending)
        else:
            masked.append(line)
    value = "".join(masked)
    names = sorted(categories)
    return (None if scanner.scan(value) else value), count, names


def _fallback_stage(task_id, fallback, acceptance, gate, ledger, config, failures=(), trail=None):
    trail = [] if trail is None else trail
    try:
        attempt = fallback(task_id)
    except LocalAttemptFailed as exc:
        failure = str(exc)
        _append(
            ledger,
            _row(
                task_id,
                "fallback",
                "claude-haiku-5-5",
                exc.input_tokens or 0,
                exc.output_tokens or 0,
                exc.usd,  # None = the proxy may have spent money we could not read
                failure,
                extra={"usd_known": exc.usd is not None},
            ),
        )
        trail.append({"stage": "fallback", "worker_failure": failure, "usd": exc.usd})
        return RelayResult("FAILED", reason="; ".join([*failures, f"fallback: {failure}"]))
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
    checked = _check_stage(task_id, attempt, acceptance, gate, ledger, config, "fallback", trail)
    reasons = [*failures]
    if checked.reason:
        reasons.append(f"fallback review: {checked.reason}")
    reason = "; ".join(reasons) or None
    if checked.route != "ACCEPTED":
        return replace(checked, route="FAILED", reason=reason)
    return replace(checked, route="FALLBACK", reason=reason)


def _verdict_reason(reply: GateReply) -> str | None:
    if reply.reason is not None:
        return str(reply.reason)
    if reply.verdict not in GATE_VERDICTS:
        return "GATE_VERDICT_UNKNOWN"
    return None if reply.verdict == "ACCEPT" else "GATE_REJECTED"


def _cost(reply: GateReply) -> float:
    return float(getattr(reply, "usd", 0.0))


def _details(result, reply) -> dict:
    """Optional failure evidence from the acceptance check and the gate (diagnostics only)."""
    out = {}
    if getattr(result, "detail", None) is not None:
        out["acceptance_detail"] = result.detail
    gate_detail = getattr(reply, "detail", None)
    if gate_detail is not None:
        out["gate_detail"] = gate_detail
    return out


def _stage(stage, result, reply, reason):
    """One stage's evidence: Core acceptance and the gate's verdict (or why it gave none)."""
    return {
        "stage": stage,
        "acceptance_passed": result.passed,
        "acceptance": str(result.raw)[:500],
        "gate_verdict": getattr(reply, "verdict", None),
        "gate_reason": reason,
        "gate_called": True,
        "gate_text": redact(str(getattr(reply, "text", "") or ""))[:300],
        **_details(result, reply),
    }


def _row(task_id, stage, model_id, input_tokens, output_tokens, usd, failure=None, extra=None):
    row = {
        "task_id": task_id,
        "stage": stage,
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
    }
    if failure is not None:
        row["failure"] = failure
    if extra:
        row.update(extra)
    return row


def _append(ledger, row):
    try:
        ledger.append(row)
    except Exception as exc:
        raise LedgerError(str(exc)) from exc


class LedgerError(RuntimeError):
    """Ledger failure; callers must stop before another cloud call."""
