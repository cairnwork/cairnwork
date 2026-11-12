"""Cut selection, plan review and resume orchestration for #476 PAUSE step 3."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experimental.local_worker import cli as worker_cli
from experimental.local_worker import write_scope as local_write_scope
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
    scope_checker: Callable[[Any, tuple[str, ...] | None], bool] | None = None,
    write_scope: tuple[str, ...] | None = None,
    reject_limit: int = 1,
    bare_runner: Callable[[], Any] | None = None,
    sweep: Callable[[], str] | None = None,
    receipt_path: Path | None = None,
    plan_dir: Path | None = None,
) -> ResumeResult:
    """Run one decision. Injected runners keep tests offline and avoid starting a worker.

    Every fallback calls ``bare_runner`` (the bare run) and records why. A REJECT or a reason
    never reaches ``resume_runner``. ``plan_dir`` is where the plan file is written (local_worker
    only reads plans inside its --out); default a temporary directory."""
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    turns, base = _turns(parent)
    cut = first_edit_cut(turns, base)
    checkpoint = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    cut_turn = cut.get("cut_turn_index")
    seen: dict[str, Any] = {"checkpoint_sha256": checkpoint, "cut_turn_index": cut_turn}

    def done(result: ResumeResult) -> ResumeResult:
        if receipt_path is not None:
            receipt.append(receipt_path, result.receipt)
        return result

    def fallback(reason: Reason | None, **extra: Any) -> ResumeResult:
        values = {**seen, **extra}
        result = bare_runner() if bare_runner else None
        rec = _receipt(task_id, events, intervened=False, reason=reason, **values)
        return done(ResumeResult(False, reason, rec, worker_result=result))

    if cut["cut_status"] != "CUT" or cut_turn is None:
        return fallback(Reason.PAUSE_NO_CUT)
    guard.events.record("cut_selected", cut_turn_index=cut_turn)
    try:
        guard.start()
        cut_digest = guard.baseline  # sweep 1, at the cut
        guard.at_plan_arrival()
    except GuardUnavailable:
        return fallback(Reason.PAUSE_GUARD_UNAVAILABLE)
    prefix, _, _ = worker_cli.resume_prefix(parent, cut_turn)
    request = PlanRequest(task_id, checkpoint, tuple(prefix), planner_settings)
    for _ in range(max(1, reject_limit)):
        planned = planner.plan(request)
        if planned.reason is not None or planned.text is None:
            return fallback(
                planned.reason or Reason.PAUSE_PLANNER_BAD_OUTPUT,
                planner_model_id=planned.model_id,
                planner_effort=planned.effort,
            )
        plan = planned.text
        seen.update(plan=plan, planner_model_id=planned.model_id, planner_effort=planned.effort)
        reviewed = gate.review(GateRequest(task_id, checkpoint, plan, gate_settings))
        seen.update(
            gate_model_id=reviewed.model_id,
            gate_effort=reviewed.effort,
            gate_verdict=reviewed.verdict,
        )
        if reviewed.reason is not None or reviewed.verdict is None:
            return fallback(reviewed.reason or Reason.PAUSE_GATE_BAD_OUTPUT)
        if reviewed.verdict == "ACCEPT":
            break
    else:
        return fallback(Reason.PAUSE_GATE_REJECT_LIMIT)
    with tempfile.TemporaryDirectory(prefix="cairnwork-pause-", dir=plan_dir) as directory:
        plan_file = Path(directory) / "plan.txt"
        plan_file.write_text(plan, encoding="utf-8")
        result = resume_runner(parent_path, cut_turn, plan_file)
    if not (scope_checker or _scope_checker)(result, write_scope):
        reason = Reason.PAUSE_SCOPE_VIOLATION  # the resumed result is discarded, no patch kept
        rec = _receipt(task_id, events, intervened=False, reason=reason, **seen)
        return done(ResumeResult(False, reason, rec))
    try:
        final_digest = sweep() if sweep is not None else guard.guard.digest()
    except GuardUnavailable:
        final_digest = None
    if cut_digest is None or final_digest != cut_digest:
        return fallback(Reason.PAUSE_GUARD_UNAVAILABLE)
    rec = _receipt(task_id, events, intervened=True, reason=None, **seen)
    return done(ResumeResult(True, None, rec, patch=result))


def _scope_checker(patch: Any, scope: tuple[str, ...] | None) -> bool:
    """Validate git raw-diff entries; modes 120000 and 160000 are never regular files."""
    allowed = local_write_scope.normalize(scope)
    if not isinstance(patch, (list, tuple)):
        return False
    for row in patch:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            return False
        if row.get("mode") in ("120000", "160000", 0o120000, 0o160000):
            return False
        try:
            path = local_write_scope.canonical_entry(row["path"])
        except ValueError:
            return False
        if allowed is not None and not any(
            path == entry or path.startswith(entry + "/") for entry in allowed
        ):
            return False
    return True


def checkout_rows(checkout: Path) -> list[dict[str, str]]:
    """The worker's final changes as {path, mode} rows: tracked changes from ``git diff --raw``
    (new modes included), untracked files from lstat (a symlink is 120000, a nested repo 160000)."""
    import os
    import stat
    import subprocess

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(checkout), *args], check=True, capture_output=True
        ).stdout

    rows = []
    fields = git("diff", "HEAD", "--raw", "-z", "--no-renames").split(b"\0")
    for meta, name in zip(fields[0::2], fields[1::2], strict=False):
        rows.append({"path": name.decode(), "mode": meta.split()[1].decode()})
    for name in git("ls-files", "-o", "--exclude-standard", "-z").split(b"\0"):
        if not name:
            continue
        mode = os.lstat(checkout / name.decode()).st_mode
        kind = "120000" if stat.S_ISLNK(mode) else "160000" if stat.S_ISDIR(mode) else "100644"
        rows.append({"path": name.decode(), "mode": kind})
    return rows


def git_scope_checker(checkout: Path) -> Callable[[Any, tuple[str, ...] | None], bool]:
    """The real scope_checker: reads the resumed checkout, ignoring the runner's return value.
    A git failure fails closed (violation)."""

    def check(_result: Any, scope: tuple[str, ...] | None) -> bool:
        try:
            return _scope_checker(checkout_rows(checkout), scope)
        except Exception:  # noqa: BLE001 - fail closed on anything
            return False

    return check
