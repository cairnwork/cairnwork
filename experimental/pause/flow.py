"""Post-hoc two-run PAUSE orchestration (#476 step 3c, Option A).

The runner owns local_worker invocation and returns its durable result metadata. Keeping that
boundary injectable makes failures replayable without a model or worker process.
"""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from experimental.pause import receipt
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, Gate, Planner, PlanRequest
from experimental.pause.schema import Reason


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


def _scope_ok(checkout: Path, scope: list[str] | None) -> bool:
    """Check every changed path and file mode in a completed R1 patch against write_scope."""
    proc = subprocess.run(
        ["git", "-C", str(checkout), "diff", "--name-only", "-z", "--no-renames", "HEAD"],
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        return False
    allowed = tuple(scope or ())
    untracked = subprocess.run(
        ["git", "-C", str(checkout), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    ignored = subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=False,
    )
    if untracked.returncode or ignored.returncode:
        return False
    tracked = {p.decode("utf-8", "surrogateescape") for p in proc.stdout.split(b"\0") if p}
    new_paths = {
        p.decode("utf-8", "surrogateescape")
        for p in (untracked.stdout + ignored.stdout).split(b"\0")
        if p
    }
    paths = tracked | new_paths
    raw = subprocess.run(
        ["git", "-C", str(checkout), "diff", "--raw", "--no-renames", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    if raw.returncode:
        return False
    # Raw mode 120000 is a symlink and 160000 a gitlink; neither is an accepted regular patch.
    for line in raw.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and any(
            mode not in ("000000", "100644", "100755") for mode in fields[1:3]
        ):
            return False
    for path in paths:
        if path == ".git" or path.startswith(".git/"):
            return False
        if path in new_paths:
            try:
                kind = (checkout / path).lstat().st_mode
            except OSError:
                return False
            if not stat.S_ISREG(kind):
                return False
        if scope is not None and not any(
            path == root or path.startswith(root + "/") for root in allowed
        ):
            return False
    return True


def _cut(record: dict | None) -> tuple[int | None, str]:
    cut = (record or {}).get("first_edit_cut", {})
    if cut.get("cut_status") != "CUT" or type(cut.get("cut_turn_index")) is not int:
        return None, "PAUSE_NO_CUT"
    return cut["cut_turn_index"], ""


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
) -> tuple[RunResult, dict]:
    """Run bare R0, then optionally plan/gate and resume in fresh R1; return the winner."""
    r0 = runner("run", list(args))
    transcript = r0.transcript
    cut, reason = _cut(r0.record)
    checkpoint = None
    plan_text = None
    pmodel = peffort = gmodel = geffort = verdict = None
    if cut is not None and transcript is not None:
        parent = json.loads(transcript.read_text(encoding="utf-8"))
        digest = checkpoint = _sha(transcript)
        turn = next((t for t in parent["turns"] if t["index"] == cut), None)
        if turn is None or turn["assistant_msg_index"] < 0:
            reason = "PAUSE_NO_CUT"
        else:
            request = PlanRequest(
                task_id,
                digest,
                tuple(parent["messages"][: turn["assistant_msg_index"]]),
                planner_settings,
            )
            try:
                reply = planner.plan(request)
            except (OSError, RuntimeError, TimeoutError):
                reply = None
                reason = str(Reason.PAUSE_PLANNER_TIMEOUT)
            if reply is None:
                pass
            else:
                pmodel, peffort = reply.model_id, reply.effort
                if reply.reason:
                    reason = str(reply.reason)
                elif reply.text is None:
                    reason = str(Reason.PAUSE_PLANNER_BAD_OUTPUT)
                else:
                    plan_text = reply.text
                    from experimental.pause.interfaces import GateRequest

                    try:
                        checked = gate.review(
                            GateRequest(task_id, digest, plan_text, gate_settings)
                        )
                    except (OSError, RuntimeError, TimeoutError):
                        checked = None
                        reason = str(Reason.PAUSE_GATE_BAD_OUTPUT)
                    if checked is not None:
                        gmodel, geffort, verdict = checked.model_id, checked.effort, checked.verdict
                        if checked.reason:
                            reason = str(checked.reason)
                        elif verdict != "ACCEPT":
                            reason = str(
                                Reason.PAUSE_GATE_REJECT_LIMIT
                                if verdict == "REJECT"
                                else Reason.PAUSE_GATE_BAD_OUTPUT
                            )
                        else:
                            try:
                                resumed = runner(
                                    "resume", list(args), parent=transcript, cut=cut, plan=plan_text
                                )
                            except (OSError, RuntimeError, TimeoutError):
                                reason = str(Reason.PAUSE_WORKER_CRASH)
                            else:
                                reason = _r1_reason(r0, resumed, parent)
                                if reason is None:
                                    return resumed, receipt.build(
                                        task_id=task_id,
                                        intervened=True,
                                        event_seq=(event_seq or (lambda _: 0))("decision"),
                                        episode_id=resumed.episode_id,
                                        checkpoint_sha256=digest,
                                        cut_turn_index=cut,
                                        plan=plan_text,
                                        planner_model_id=pmodel,
                                        planner_effort=peffort,
                                        gate_model_id=gmodel,
                                        gate_effort=geffort,
                                        gate_verdict="ACCEPT",
                                        reason=None,
                                    )
    rec = receipt.build(
        task_id=task_id,
        intervened=False,
        event_seq=(event_seq or (lambda _: 0))("decision"),
        episode_id=r0.episode_id,
        checkpoint_sha256=checkpoint,
        cut_turn_index=cut,
        plan=plan_text,
        planner_model_id=pmodel,
        planner_effort=peffort,
        gate_model_id=gmodel,
        gate_effort=geffort,
        gate_verdict=verdict,
        reason=reason or str(Reason.PAUSE_NO_CUT),
    )
    return r0, rec


def _write_scope(parent: dict) -> list[str] | None:
    scope = parent.get("write_scope")
    return scope if isinstance(scope, list) else None


def _r1_reason(r0: RunResult, r1: RunResult, parent: dict) -> str | None:
    if r1.root.resolve() == r0.root.resolve() or r1.checkout.resolve() == r0.checkout.resolve():
        return str(Reason.PAUSE_SCOPE_VIOLATION)
    if r1.guard_before_plan is None or r1.guard_before_plan != r1.guard_at_plan_arrival:
        return str(Reason.PAUSE_GUARD_UNAVAILABLE)
    try:
        if not _scope_ok(r1.checkout, _write_scope(parent)):
            return str(Reason.PAUSE_SCOPE_VIOLATION)
    except (OSError, ValueError, RuntimeError):
        return str(Reason.PAUSE_GUARD_UNAVAILABLE)
    return None if r1.code == 0 else str(Reason.PAUSE_WORKER_CRASH)


def _checkout_digest(checkout: Path) -> str:
    """AC8 digest via the filesystem walker, including ignored files, modes and symlinks."""
    return TreeGuard(checkout).digest()
