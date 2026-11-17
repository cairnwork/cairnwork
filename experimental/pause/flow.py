"""Post-hoc two-run PAUSE orchestration (#476 step 3c, Option A).

The runner owns local_worker invocation and returns its durable result metadata. Keeping that
boundary injectable makes failures replayable without a model or worker process. R1 wins only when
its completed, spec-sufficient patch is marked exactly VERIFIED by Core.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from experimental.pause import receipt
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import (
    CallSettings,
    Gate,
    GateReply,
    Planner,
    PlanReply,
    PlanRequest,
)
from experimental.pause.schema import GATE_VERDICTS, Reason
from experimental.pause.scope import _scope_ok

LOCAL_UNREACHABLE_EXIT = 3  # == EndpointUnreachable.exit_code (pinned by a test)


@dataclass(frozen=True)
class RunResult:
    code: int
    output: str
    root: Path
    checkout: Path
    transcript: Path | None = None
    record: dict | None = None
    episode_id: str | None = None
    guard_before_plan: str | None = None
    guard_at_plan_arrival: str | None = None
    base_sha: str | None = None  # the pinned base the runner checked R1 out at (Core's base)
    git_config_sha256: str | None = None


@dataclass(frozen=True)
class CoreVerdict:
    status: str
    tree_sha256: str


class Runner(Protocol):
    def __call__(
        self,
        mode: str,
        args: list[str],
        *,
        parent: Path | None = None,
        cut: int | None = None,
        plan: str | None = None,
    ) -> RunResult: ...


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cut(record: dict | None) -> tuple[int | None, str]:
    cut = (record or {}).get("first_edit_cut", {})
    if cut.get("cut_status") != "CUT" or type(cut.get("cut_turn_index")) is not int:
        return None, "PAUSE_NO_CUT"
    return cut["cut_turn_index"], ""


def _empty_info() -> dict[str, str | None]:
    return dict.fromkeys(
        (
            "plan",
            "planner_model_id",
            "planner_effort",
            "gate_model_id",
            "gate_effort",
            "gate_verdict",
            "core_verdict",
            "core_tree_sha256",
        )
    )


def _load_parent(transcript: Path) -> dict | None:
    """R0's transcript, or None when it is unreadable or lacks turns/messages (R0 stays)."""
    try:
        parent = json.loads(transcript.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (
        isinstance(parent, dict)
        and isinstance(parent.get("turns"), list)
        and isinstance(parent.get("messages"), list)
    )
    return parent if ok else None


def _attempt_resume(
    r0,
    parent,
    cut,
    digest,
    task_id,
    planner,
    gate,
    planner_settings,
    gate_settings,
    runner,
    args,
    core_verdict,
):
    from experimental.pause.interfaces import GateRequest

    info = _empty_info()
    turn = next((item for item in parent["turns"] if item["index"] == cut), None)
    if turn is None or turn["assistant_msg_index"] < 0:
        return None, info, str(Reason.PAUSE_NO_CUT)
    request = PlanRequest(
        task_id, digest, tuple(parent["messages"][: turn["assistant_msg_index"]]), planner_settings
    )
    try:
        reply = planner.plan(request)
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return None, info, str(Reason.PAUSE_PLANNER_TIMEOUT)
    if not isinstance(reply, PlanReply):
        return None, info, str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
    info.update(planner_model_id=reply.model_id, planner_effort=reply.effort)
    if reply.reason:
        return None, info, str(reply.reason)
    if reply.text is None:
        return None, info, str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
    info["plan"] = reply.text
    try:
        checked = gate.review(GateRequest(task_id, digest, reply.text, gate_settings))
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return None, info, str(Reason.PAUSE_GATE_BAD_OUTPUT)
    if not isinstance(checked, GateReply):
        return None, info, str(Reason.PAUSE_GATE_BAD_OUTPUT)
    info.update(
        gate_model_id=checked.model_id,
        gate_effort=checked.effort,
        # an unknown verdict is not recorded: the receipt only carries ACCEPT or REJECT
        gate_verdict=checked.verdict if checked.verdict in GATE_VERDICTS else None,
    )
    if checked.reason:
        return None, info, str(checked.reason)
    if checked.verdict != "ACCEPT":
        reason = (
            Reason.PAUSE_GATE_REJECT_LIMIT
            if checked.verdict == "REJECT"
            else Reason.PAUSE_GATE_BAD_OUTPUT
        )
        return None, info, str(reason)
    try:
        resumed = runner("resume", list(args), parent=r0.transcript, cut=cut, plan=reply.text)
    except (Exception, SystemExit):  # noqa: BLE001 - R1 failures keep R0.
        return None, info, str(Reason.PAUSE_WORKER_CRASH)
    if not isinstance(resumed, RunResult):
        return None, info, str(Reason.PAUSE_WORKER_CRASH)
    reason, verdict, tree = _r1_reason(r0, resumed, parent, core_verdict)
    info.update(core_verdict=verdict, core_tree_sha256=tree)
    return resumed, info, reason


def run_flow(
    args: list[str],
    *,
    task_id: str,
    planner: Planner,
    gate: Gate,
    planner_settings: CallSettings,
    gate_settings: CallSettings,
    runner: Runner,
    event_seq: Callable[[str], int] | None = None,
    core_verdict: Callable[[RunResult], CoreVerdict] | None = None,
) -> tuple[RunResult, dict]:
    """Run bare R0, then optionally plan/gate and resume in fresh R1; return the winner."""
    r0 = runner("run", list(args))
    transcript = r0.transcript
    cut, reason = _cut(r0.record)
    checkpoint = None
    info = _empty_info()
    if cut is not None and transcript is not None:
        parent = _load_parent(transcript)
        if parent is None:
            reason = str(Reason.PAUSE_NO_CUT)
        else:
            digest = checkpoint = _sha(transcript)
            resumed, info, reason = _attempt_resume(
                r0,
                parent,
                cut,
                digest,
                task_id,
                planner,
                gate,
                planner_settings,
                gate_settings,
                runner,
                args,
                core_verdict,
            )
            if resumed is not None and reason is None:
                return resumed, receipt.build(
                    task_id=task_id,
                    intervened=True,
                    event_seq=(event_seq or (lambda _: 0))("decision"),
                    episode_id=resumed.episode_id,
                    checkpoint_sha256=digest,
                    cut_turn_index=cut,
                    reason=None,
                    **info,
                )
    rec = receipt.build(
        task_id=task_id,
        intervened=False,
        event_seq=(event_seq or (lambda _: 0))("decision"),
        episode_id=r0.episode_id,
        checkpoint_sha256=checkpoint,
        cut_turn_index=cut,
        **info,
        reason=reason or str(Reason.PAUSE_NO_CUT),
    )
    return r0, rec


def _write_scope(parent: dict) -> list[str] | None | str:
    scope = parent.get("write_scope")
    if scope is None:
        return None
    if not isinstance(scope, list) or not all(type(item) is str for item in scope):
        return str(Reason.PAUSE_SCOPE_VIOLATION)
    return scope


def _r1_reason(
    r0: RunResult,
    r1: RunResult,
    parent: dict,
    core_verdict: Callable[[RunResult], CoreVerdict] | None,
) -> tuple[str | None, str | None, str | None]:
    try:
        same_root = r1.root.resolve() == r0.root.resolve()
        same_checkout = r1.checkout.resolve() == r0.checkout.resolve()
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return str(Reason.PAUSE_GUARD_UNAVAILABLE), None, None
    if same_root or same_checkout:
        return str(Reason.PAUSE_SCOPE_VIOLATION), None, None
    if r1.guard_before_plan is None or r1.guard_before_plan != r1.guard_at_plan_arrival:
        return str(Reason.PAUSE_GUARD_UNAVAILABLE), None, None
    try:
        scope = _write_scope(parent)
        if isinstance(scope, str):
            return scope, None, None
        base = (r0.record or {}).get("base_sha")
        if (
            type(base) is not str
            or len(base) != 40
            or any(c not in "0123456789abcdef" for c in base)
        ):
            return str(Reason.PAUSE_SCOPE_VIOLATION), None, None
        if r1.base_sha is not None and r1.base_sha != base:
            return str(Reason.PAUSE_SCOPE_VIOLATION), None, None
        if not _scope_ok(r1.checkout, scope, base, r1.git_config_sha256):
            return str(Reason.PAUSE_SCOPE_VIOLATION), None, None
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return str(Reason.PAUSE_GUARD_UNAVAILABLE), None, None
    if r1.code == LOCAL_UNREACHABLE_EXIT:
        return str(Reason.PAUSE_LOCAL_UNREACHABLE), None, None
    if r1.code != 0:
        return str(Reason.PAUSE_WORKER_CRASH), None, None
    record = r1.record
    if not isinstance(record, dict):
        return str(Reason.PAUSE_WORKER_CRASH), None, None
    verification = record.get("verification")
    feedback = record.get("spec_feedback")
    if (
        not isinstance(verification, dict)
        or verification.get("exit_reason") != "COMPLETED"
        or (isinstance(feedback, dict) and feedback.get("needs_spec") is True)
    ):
        if _endpoint_unreachable(record):
            return str(Reason.PAUSE_LOCAL_UNREACHABLE), None, None
        return str(Reason.PAUSE_R1_INCOMPLETE), None, None
    if core_verdict is None:
        return str(Reason.PAUSE_R1_NOT_VERIFIED), None, None
    try:
        before = _checkout_digest(r1.checkout)
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return str(Reason.PAUSE_GUARD_UNAVAILABLE), None, None
    try:
        verdict = core_verdict(r1)
    except (Exception, SystemExit, KeyboardInterrupt):  # noqa: BLE001 - Core failures keep R0.
        return str(Reason.PAUSE_R1_NOT_VERIFIED), None, None
    try:
        after = _checkout_digest(r1.checkout)
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return str(Reason.PAUSE_GUARD_UNAVAILABLE), None, None
    status = (
        verdict.status if isinstance(verdict, CoreVerdict) and type(verdict.status) is str else None
    )
    tree = (
        verdict.tree_sha256
        if isinstance(verdict, CoreVerdict) and type(verdict.tree_sha256) is str
        else None
    )
    valid_tree = tree is not None and len(tree) == 64 and all(c in "0123456789abcdef" for c in tree)
    if status != "VERIFIED" or tree != before or before != after:
        if status is None or status == "VERIFIED" or not valid_tree:
            return str(Reason.PAUSE_R1_NOT_VERIFIED), None, None
        return str(Reason.PAUSE_R1_NOT_VERIFIED), status, tree
    return None, status, tree


def _endpoint_unreachable(record: dict) -> bool:
    """A worker that aborted because the local endpoint could not be connected to (#1406)."""
    verification = record.get("verification")
    termination = record.get("termination")
    return (
        isinstance(verification, dict)
        and verification.get("exit_reason") == "ERROR_ABORT"
        and isinstance(termination, dict)
        and termination.get("abort_cause") == "endpoint_unreachable"
    )


def _checkout_digest(checkout: Path) -> str:
    """AC8 digest via the filesystem walker, including ignored files, modes and symlinks."""
    return TreeGuard(checkout).digest()
