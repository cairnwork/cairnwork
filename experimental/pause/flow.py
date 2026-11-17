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
from typing import Any, Protocol

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
from experimental.pause.schema import FALLBACK_REASONS, GATE_VERDICTS, Reason
from experimental.pause.scope import _scope_ok


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
    if not isinstance(record, dict):
        return None, "PAUSE_NO_CUT"
    cut = record.get("first_edit_cut")
    if not isinstance(cut, dict):
        return None, "PAUSE_NO_CUT"
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
    if not isinstance(transcript, Path):
        return None
    try:
        parent = json.loads(transcript.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    ok = (
        isinstance(parent, dict)
        and isinstance(parent.get("turns"), list)
        and isinstance(parent.get("messages"), list)
    )
    return parent if ok else None


def _plan_stage(parent, cut, digest, task_id, planner, planner_settings, info):
    """Plan R1 from the cut. Returns (reply, reason); fills info in place."""
    turns = parent["turns"]
    if any(
        not isinstance(item, dict)
        or type(item.get("index")) is not int
        or type(item.get("assistant_msg_index")) is not int
        for item in turns
    ):
        return None, str(Reason.PAUSE_NO_CUT)
    turn = next((item for item in turns if item["index"] == cut), None)
    if turn is None or turn["assistant_msg_index"] < 0:
        return None, str(Reason.PAUSE_NO_CUT)
    request = PlanRequest(
        task_id, digest, tuple(parent["messages"][: turn["assistant_msg_index"]]), planner_settings
    )
    try:
        reply = planner.plan(request)
    except (Exception, SystemExit):  # noqa: BLE001 - R1 failures keep R0.
        return None, str(Reason.PAUSE_PLANNER_TIMEOUT)
    if not isinstance(reply, PlanReply):
        return None, str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
    bad_planner_metadata = type(reply.model_id) is not str or type(reply.effort) is not str
    info.update(planner_model_id=reply.model_id, planner_effort=reply.effort)
    for key in ("planner_model_id", "planner_effort"):
        if type(info[key]) is not str:
            info[key] = None
    known_reasons = {str(reason) for reason in FALLBACK_REASONS}
    if (
        bad_planner_metadata
        or type(reply.text) not in (str, type(None))
        or (
            reply.reason is not None
            and (type(reply.reason) not in (str, Reason) or str(reply.reason) not in known_reasons)
        )
    ):
        return None, str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
    if reply.reason:
        return None, str(reply.reason)
    if reply.text is None:
        return None, str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
    try:
        reply.text.encode("utf-8")
    except UnicodeEncodeError:
        return None, str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
    info["plan"] = reply.text
    return reply, None


def _gate_stage(task_id, digest, plan_text, gate, gate_settings, info):
    """Review the plan. Returns (checked, reason); fills info in place."""
    from experimental.pause.interfaces import GateRequest

    try:
        checked = gate.review(GateRequest(task_id, digest, plan_text, gate_settings))
    except (Exception, SystemExit):  # noqa: BLE001 - R1 failures keep R0.
        return None, str(Reason.PAUSE_GATE_BAD_OUTPUT)
    if not isinstance(checked, GateReply):
        return None, str(Reason.PAUSE_GATE_BAD_OUTPUT)
    bad_gate_metadata = type(checked.model_id) is not str or type(checked.effort) is not str
    info.update(
        gate_model_id=checked.model_id,
        gate_effort=checked.effort,
        # an unknown verdict is not recorded: the receipt only carries ACCEPT or REJECT
        gate_verdict=checked.verdict if checked.verdict in GATE_VERDICTS else None,
    )
    for key in ("gate_model_id", "gate_effort"):
        if type(info[key]) is not str:
            info[key] = None
    known_reasons = {str(reason) for reason in FALLBACK_REASONS}
    if (
        bad_gate_metadata
        or (
            checked.reason is not None
            and (
                type(checked.reason) not in (str, Reason)
                or str(checked.reason) not in known_reasons
            )
        )
        or (checked.verdict is not None and checked.verdict not in GATE_VERDICTS)
    ):
        return None, str(Reason.PAUSE_GATE_BAD_OUTPUT)
    if checked.reason:
        return None, str(checked.reason)
    if checked.verdict != "ACCEPT":
        reason = (
            Reason.PAUSE_GATE_REJECT_LIMIT
            if checked.verdict == "REJECT"
            else Reason.PAUSE_GATE_BAD_OUTPUT
        )
        return None, str(reason)
    return checked, None


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
    info = _empty_info()
    reply, reason = _plan_stage(parent, cut, digest, task_id, planner, planner_settings, info)
    if reply is None:
        return None, info, reason
    _checked, reason = _gate_stage(task_id, digest, reply.text, gate, gate_settings, info)
    if reason is not None:
        return None, info, reason
    try:
        resumed = runner("resume", list(args), parent=r0.transcript, cut=cut, plan=reply.text)
    except (Exception, SystemExit):  # noqa: BLE001 - R1 failures keep R0.
        return None, info, str(Reason.PAUSE_WORKER_CRASH)
    if not isinstance(resumed, RunResult):
        return None, info, str(Reason.PAUSE_WORKER_CRASH)
    reason, verdict, tree = _r1_reason(r0, resumed, parent, core_verdict)
    info.update(core_verdict=verdict, core_tree_sha256=tree)
    return resumed, info, reason


def _fallback_receipt(values: dict[str, Any], info: dict) -> dict:
    """R0's receipt; stage details that cannot be recorded give way to R0-only data."""
    try:
        return receipt.build(**values, **info)
    except Exception:  # noqa: BLE001 - unrecordable stage details must not drop R0.
        if values["reason"] not in {str(r) for r in FALLBACK_REASONS}:
            values["reason"] = str(Reason.PAUSE_NO_CUT)
        return receipt.build(**{**values, **_empty_info()})


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
                try:
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
                except (Exception, SystemExit):  # noqa: BLE001 - unrecordable R1 falls back to R0.
                    reason = str(Reason.PAUSE_R1_NOT_VERIFIED)
                    info.update(core_verdict=None, core_tree_sha256=None)
    values = dict(
        task_id=task_id,
        intervened=False,
        event_seq=(event_seq or (lambda _: 0))("decision"),
        episode_id=r0.episode_id,
        checkpoint_sha256=checkpoint,
        cut_turn_index=cut,
        reason=reason or str(Reason.PAUSE_NO_CUT),
    )
    return r0, _fallback_receipt(values, info)


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
        return str(Reason.PAUSE_R1_INCOMPLETE), None, None
    if core_verdict is None:
        return str(Reason.PAUSE_R1_NOT_VERIFIED), None, None
    try:
        before = _checkout_digest(r1.checkout)
    except Exception:  # noqa: BLE001 - R1 failures keep R0.
        return str(Reason.PAUSE_GUARD_UNAVAILABLE), None, None
    try:
        verdict = core_verdict(r1)
    except (Exception, SystemExit):  # noqa: BLE001 - Core failures keep R0.
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


def _checkout_digest(checkout: Path) -> str:
    """AC8 digest via the filesystem walker, including ignored files, modes and symlinks."""
    return TreeGuard(checkout).digest()
