"""Cut selection, plan review and resume orchestration for #476 PAUSE step 3."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experimental.local_worker.cuts import first_edit_cut
from experimental.local_worker.gitstate import TreeState
from experimental.pause import receipt
from experimental.pause.controller import PrePlanGuard
from experimental.pause.interfaces import (
    CallSettings,
    EventLog,
    Gate,
    GateRequest,
    GuardUnavailable,
    Planner,
    PlanRequest,
)
from experimental.pause.schema import Reason


@dataclass(frozen=True)
class ResumeResult:
    intervened: bool
    reason: Reason | None
    receipt: dict[str, Any]
    patch: Any = None
    worker_result: Any = None


def _turns(parent: dict) -> tuple[list, TreeState]:
    from experimental.local_worker.episode import TurnLog

    turns = []
    for row in parent["turns"]:
        data = dict(row)
        tree = data.get("tree")
        data["tree"] = (
            TreeState(tree is not None, tree.get("head"), tuple(tree.get("entries", ())))
            if tree is not None
            else None
        )
        for key in ("tokens",):
            if isinstance(data.get(key), list):
                data[key] = tuple(data[key])
        turns.append(TurnLog(**data))
    record = parent.get("record", {})
    return turns, TreeState(True, record.get("base_sha"), ())


def _receipt(
    task_id: str,
    events: EventLog,
    *,
    intervened: bool,
    reason: Reason | None,
    **values: Any,
) -> dict[str, Any]:
    seq = events.record("decision", task_id=task_id, reason=str(reason) if reason else None)
    return receipt.build(
        task_id=task_id,
        intervened=intervened,
        reason=reason,
        event_seq=seq,
        **values,
    )


def run_resume(
    parent_path: Path,
    *,
    task_id: str,
    planner: Planner,
    gate: Gate,
    planner_settings: CallSettings,
    gate_settings: CallSettings,
    guard: PrePlanGuard,
    events: EventLog,
    resume_runner: Callable[[Path, int, Path], Any],
    scope_checker: Callable[[Any, tuple[str, ...] | None], bool],
    write_scope: tuple[str, ...] | None = None,
    reject_limit: int = 1,
    bare_runner: Callable[[], Any] | None = None,
) -> ResumeResult:
    """Run one decision. Injected runners keep tests offline and avoid starting a worker."""
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    turns, base = _turns(parent)
    cut = first_edit_cut(turns, base)
    checkpoint = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    if cut["cut_status"] != "CUT":
        reason = Reason.PAUSE_NO_CUT
        result = bare_runner() if bare_runner else None
        rec = _receipt(
            task_id,
            events,
            intervened=False,
            reason=reason,
            checkpoint_sha256=checkpoint,
            cut_turn_index=cut.get("cut_turn_index"),
        )
        return ResumeResult(False, reason, rec, worker_result=result)
    cut_turn = cut["cut_turn_index"]
    guard.events.record("cut_selected", cut_turn_index=cut_turn)
    try:
        guard.start()
        cut_digest = guard.baseline
        guard.at_plan_arrival()
    except GuardUnavailable:
        reason = Reason.PAUSE_GUARD_UNAVAILABLE
        result = bare_runner() if bare_runner else None
        rec = _receipt(
            task_id,
            events,
            intervened=False,
            reason=reason,
            checkpoint_sha256=checkpoint,
            cut_turn_index=cut_turn,
        )
        return ResumeResult(False, reason, rec, worker_result=result)
    prefix, _, _ = __import__(
        "experimental.local_worker.cli", fromlist=["resume_prefix"]
    ).resume_prefix(parent, cut_turn)
    planned = planner.plan(PlanRequest(task_id, checkpoint, tuple(prefix), planner_settings))
    if planned.reason is not None or planned.text is None:
        result = bare_runner() if bare_runner else None
        rec = _receipt(
            task_id,
            events,
            intervened=False,
            reason=planned.reason,
            checkpoint_sha256=checkpoint,
            cut_turn_index=cut_turn,
            planner_model_id=planned.model_id,
            planner_effort=planned.effort,
        )
        return ResumeResult(False, planned.reason, rec, worker_result=result)
    plan = planned.text
    plan_hash = hashlib.sha256(plan.encode("utf-8")).hexdigest()
    reviewed = gate.review(GateRequest(task_id, checkpoint, plan, gate_settings))
    reason = reviewed.reason
    if reason is None and reviewed.verdict != "ACCEPT":
        reason = Reason.PAUSE_GATE_REJECT_LIMIT
    if reason is not None:
        result = bare_runner() if bare_runner else None
        rec = _receipt(
            task_id,
            events,
            intervened=False,
            reason=reason,
            checkpoint_sha256=checkpoint,
            cut_turn_index=cut_turn,
            plan_sha256=plan_hash,
            planner_model_id=planned.model_id,
            planner_effort=planned.effort,
            gate_model_id=reviewed.model_id,
            gate_effort=reviewed.effort,
            gate_verdict=reviewed.verdict,
        )
        return ResumeResult(False, reason, rec, worker_result=result)
    with tempfile.TemporaryDirectory(prefix="cairnwork-pause-") as directory:
        plan_file = Path(directory) / "plan.txt"
        plan_file.write_text(plan, encoding="utf-8")
        result = resume_runner(parent_path, cut_turn, plan_file)
    try:
        if cut_digest is None or guard.guard.digest() != cut_digest:
            reason = Reason.PAUSE_GUARD_UNAVAILABLE
            rec = _receipt(
                task_id,
                events,
                intervened=False,
                reason=reason,
                checkpoint_sha256=checkpoint,
                cut_turn_index=cut_turn,
                plan_sha256=plan_hash,
                planner_model_id=planned.model_id,
                planner_effort=planned.effort,
                gate_model_id=reviewed.model_id,
                gate_effort=reviewed.effort,
                gate_verdict=reviewed.verdict,
            )
            return ResumeResult(False, reason, rec)
    except GuardUnavailable:
        reason = Reason.PAUSE_GUARD_UNAVAILABLE
        rec = _receipt(
            task_id,
            events,
            intervened=False,
            reason=reason,
            checkpoint_sha256=checkpoint,
            cut_turn_index=cut_turn,
            plan_sha256=plan_hash,
            planner_model_id=planned.model_id,
            planner_effort=planned.effort,
            gate_model_id=reviewed.model_id,
            gate_effort=reviewed.effort,
            gate_verdict=reviewed.verdict,
        )
        return ResumeResult(False, reason, rec)
    if not scope_checker(result, write_scope):
        reason = Reason.PAUSE_SCOPE_VIOLATION
        rec = _receipt(
            task_id,
            events,
            intervened=False,
            reason=reason,
            checkpoint_sha256=checkpoint,
            cut_turn_index=cut_turn,
            plan_sha256=plan_hash,
            planner_model_id=planned.model_id,
            planner_effort=planned.effort,
            gate_model_id=reviewed.model_id,
            gate_effort=reviewed.effort,
            gate_verdict=reviewed.verdict,
        )
        return ResumeResult(False, reason, rec)
    rec = _receipt(
        task_id,
        events,
        intervened=True,
        reason=None,
        checkpoint_sha256=checkpoint,
        cut_turn_index=cut_turn,
        plan_sha256=plan_hash,
        planner_model_id=planned.model_id,
        planner_effort=planned.effort,
        gate_model_id=reviewed.model_id,
        gate_effort=reviewed.effort,
        gate_verdict="ACCEPT",
    )
    return ResumeResult(True, None, rec, patch=result)
