from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from experimental.pause import receipt
from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply

SETTINGS = CallSettings("m", "low", 1.0, 1)


class Planner:
    def __init__(self, result: Any = None, error: BaseException | None = None):
        self.result, self.error = result, error

    def plan(self, request: Any) -> Any:
        if self.error:
            raise self.error
        return self.result or PlanReply("plan", "p", "low")


class Gate:
    def __init__(self, result: Any = None, error: BaseException | None = None):
        self.result, self.error = result, error

    def review(self, request: Any) -> Any:
        if self.error:
            raise self.error
        return self.result or GateReply("ACCEPT", "g", "low")


def _case(tmp_path: Path) -> tuple[RunResult, RunResult]:
    checkout = tmp_path / "R1" / "checkout"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "-C", str(checkout), "init", "-q"], check=True)
    (checkout / "src").mkdir()
    (checkout / "src/a.py").write_text("base\n")
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.name=test",
            "-c",
            "user.email=t@invalid",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    base = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    transcript = tmp_path / "parent.json"
    transcript.write_text(
        json.dumps(
            {
                "messages": [{"role": "user", "content": "task"}],
                "turns": [{"index": 2, "assistant_msg_index": 1}],
                "write_scope": ["src"],
            }
        )
    )
    r0 = RunResult(
        7,
        "R0",
        tmp_path / "R0",
        tmp_path / "R0/checkout",
        transcript,
        {"first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 2}, "base_sha": base},
        "ep0",
    )
    r1 = RunResult(
        0,
        "R1",
        tmp_path / "R1",
        checkout,
        transcript,
        {"verification": {"exit_reason": "COMPLETED"}},
        "ep1",
        "same",
        "same",
        base,
        hashlib.sha256((checkout / ".git/config").read_bytes()).hexdigest(),
    )
    return r0, r1


def _run(r0: RunResult, r1: RunResult, *, planner=None, gate=None, runner=None, judge=None):
    return run_flow(
        [],
        task_id="task",
        planner=planner or Planner(),
        gate=gate or Gate(),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner or (lambda mode, args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=judge or (lambda _result: CoreVerdict("REJECTED", "0" * 64)),
    )


def _assert_fallback(result: RunResult, rec: dict, reason: str) -> None:
    assert result.code == 7 and result.episode_id == "ep0"
    assert rec["intervened"] is False and rec["reason"] == reason
    assert receipt.verify(rec) == []


def test_plan_reason_unknown_falls_back(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, planner=Planner(PlanReply("plan", "p", "low", "UNKNOWN")))  # type: ignore[arg-type]
    _assert_fallback(result, rec, "PAUSE_PLANNER_BAD_OUTPUT")


def test_plan_text_wrong_type_falls_back(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, planner=Planner(PlanReply(123, "p", "low")))  # type: ignore[arg-type]
    _assert_fallback(result, rec, "PAUSE_PLANNER_BAD_OUTPUT")


def test_gate_reason_unknown_falls_back(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, gate=Gate(GateReply("ACCEPT", "g", "low", "UNKNOWN")))  # type: ignore[arg-type]
    _assert_fallback(result, rec, "PAUSE_GATE_BAD_OUTPUT")


def test_planner_systemexit_falls_back(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, planner=Planner(error=SystemExit(3)))
    _assert_fallback(result, rec, "PAUSE_PLANNER_TIMEOUT")


def test_gate_systemexit_falls_back(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, gate=Gate(error=SystemExit(3)))
    _assert_fallback(result, rec, "PAUSE_GATE_BAD_OUTPUT")


def test_core_keyboardinterrupt_propagates(tmp_path):
    r0, r1 = _case(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        _run(r0, r1, judge=lambda _result: (_ for _ in ()).throw(KeyboardInterrupt()))


def test_planner_keyboardinterrupt_propagates(tmp_path):
    r0, r1 = _case(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        _run(r0, r1, planner=Planner(error=KeyboardInterrupt()))


def test_gate_keyboardinterrupt_propagates(tmp_path):
    r0, r1 = _case(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        _run(r0, r1, gate=Gate(error=KeyboardInterrupt()))


def test_runner_keyboardinterrupt_propagates(tmp_path):
    r0, r1 = _case(tmp_path)

    def runner(mode, args, **_kwargs):
        if mode == "resume":
            raise KeyboardInterrupt
        return r0

    with pytest.raises(KeyboardInterrupt):
        _run(r0, r1, runner=runner)
