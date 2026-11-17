from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from experimental.pause import flow, receipt
from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply

SETTINGS = CallSettings("m", "low", 1.0, 1)


class Planner:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def plan(self, request: Any) -> PlanReply:
        if self.error:
            raise self.error
        return PlanReply("plan", "p", "low")


class Gate:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def review(self, request: Any) -> GateReply:
        if self.error:
            raise self.error
        return GateReply("ACCEPT", "g", "low")


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


def _assert_fallback(result, rec, reason):
    assert result.code == 7 and result.episode_id == "ep0"
    assert rec["intervened"] is False and rec["reason"] == reason
    assert set(rec) == set(receipt.RECEIPT_FIELDS)
    assert receipt.verify(rec) == []


def test_r1_planner_calledprocesserror_keeps_r0(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, planner=Planner(subprocess.CalledProcessError(1, "planner")))
    _assert_fallback(result, rec, "PAUSE_PLANNER_TIMEOUT")


def test_r1_planner_valueerror_keeps_r0(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, planner=Planner(ValueError("planner")))
    _assert_fallback(result, rec, "PAUSE_PLANNER_TIMEOUT")


def test_r1_gate_keyerror_keeps_r0(tmp_path):
    r0, r1 = _case(tmp_path)
    result, rec = _run(r0, r1, gate=Gate(KeyError("gate")))
    _assert_fallback(result, rec, "PAUSE_GATE_BAD_OUTPUT")


def test_r1_runner_indexerror_keeps_r0(tmp_path):
    r0, r1 = _case(tmp_path)

    def runner(mode, args, **_kwargs):
        if mode == "resume":
            raise IndexError("worker")
        return r0

    result, rec = _run(r0, r1, runner=runner)
    _assert_fallback(result, rec, "PAUSE_WORKER_CRASH")


def test_r1_core_keyerror_keeps_r0(tmp_path):
    r0, r1 = _case(tmp_path)

    def judge(_result):
        raise KeyError("core")

    result, rec = _run(r0, r1, judge=judge)
    _assert_fallback(result, rec, "PAUSE_R1_NOT_VERIFIED")


def test_r1_checkout_digest_calledprocesserror_keeps_r0(tmp_path, monkeypatch):
    r0, r1 = _case(tmp_path)

    def fail(_checkout):
        raise subprocess.CalledProcessError(1, "digest")

    monkeypatch.setattr(flow, "_checkout_digest", fail)
    result, rec = _run(r0, r1)
    _assert_fallback(result, rec, "PAUSE_GUARD_UNAVAILABLE")


def test_r1_scope_ok_keyerror_keeps_r0(tmp_path, monkeypatch):
    r0, r1 = _case(tmp_path)

    def fail(*args, **_kwargs):
        raise KeyError("scope")

    monkeypatch.setattr(flow, "_scope_ok", fail)
    result, rec = _run(r0, r1)
    _assert_fallback(result, rec, "PAUSE_GUARD_UNAVAILABLE")


class _BrokenPath:
    def resolve(self):
        raise RuntimeError("resolve")


def test_r1_resolve_runtimeerror_keeps_r0(tmp_path):
    import dataclasses

    r0, r1 = _case(tmp_path)
    broken = dataclasses.replace(r1, root=_BrokenPath())  # type: ignore[arg-type]
    result, rec = _run(r0, broken)
    _assert_fallback(result, rec, "PAUSE_GUARD_UNAVAILABLE")
