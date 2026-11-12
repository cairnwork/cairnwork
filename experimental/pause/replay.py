"""Read-only offline candidate replay through a PAUSE gate (#476 step 2)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from experimental.pause.interfaces import CallSettings, Gate, GateRequest


@dataclass(frozen=True)
class ReplayResult:
    task_id: str
    verdict: str | None
    reason: str | None


@dataclass(frozen=True)
class ReplayReport:
    results: tuple[ReplayResult, ...]
    agreement: float | None


def replay(candidate_dir: str | Path, gate: Gate, settings: CallSettings) -> ReplayReport:
    """Replay each child plan once; read files only and never create run state."""
    root = Path(candidate_dir)
    results: list[ReplayResult] = []
    matches = total = 0
    for task in sorted(path for path in root.iterdir() if path.is_dir()):
        plan = (task / "plan.txt").read_text(encoding="utf-8")
        checkpoint_path = task / "checkpoint.sha256"
        checkpoint = (
            checkpoint_path.read_text(encoding="utf-8").strip()
            if checkpoint_path.exists()
            else hashlib.sha256(plan.encode()).hexdigest()
        )
        reply = gate.review(GateRequest(task.name, checkpoint, plan, settings))
        results.append(
            ReplayResult(task.name, reply.verdict, reply.reason.value if reply.reason else None)
        )
        expected_path = task / "expected_verdict.txt"
        if expected_path.exists():
            total += 1
            matches += reply.verdict == expected_path.read_text(encoding="utf-8").strip()
    return ReplayReport(tuple(results), matches / total if total else None)
