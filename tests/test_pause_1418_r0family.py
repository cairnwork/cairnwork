from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from experimental.pause import cli, flow, receipt
from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply

SETTINGS = CallSettings("m", "low", 1.0, 1)


class Planner:
    def __init__(self, result: Any = None):
        self.result = result

    def plan(self, request: Any) -> Any:
        return self.result or PlanReply("plan", "p", "low")


class Gate:
    def review(self, request: Any) -> Any:
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
    _transcript(transcript, [{"index": 2, "assistant_msg_index": 1}])
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


def _transcript(path: Path, turns: Any) -> None:
    path.write_text(
        json.dumps(
            {
                "messages": [{"role": "user", "content": "task"}],
                "turns": turns,
                "write_scope": ["src"],
            }
        )
    )


def _run(r0: RunResult, r1: RunResult, *, planner: Any = None, judge: Any = None):
    return run_flow(
        [],
        task_id="task",
        planner=planner or Planner(),
        gate=Gate(),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=lambda mode, args, **_kwargs: r0 if mode == "run" else r1,
        core_verdict=judge or (lambda _r: CoreVerdict("REJECTED", "0" * 64)),
    )


def _assert_fallback(result: RunResult, rec: dict, reason: str) -> None:
    assert result.code == 7 and result.episode_id == "ep0"
    assert rec["intervened"] is False and rec["reason"] == reason
    assert receipt.verify(rec) == []


def test_turn_item_not_dict_keeps_r0(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    _transcript(r0.transcript, [None])  # type: ignore[arg-type]
    _assert_fallback(*_run(r0, r1), "PAUSE_NO_CUT")


def test_turn_assistant_index_string_keeps_r0(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    _transcript(r0.transcript, [{"index": 2, "assistant_msg_index": "1"}])  # type: ignore[arg-type]
    _assert_fallback(*_run(r0, r1), "PAUSE_NO_CUT")


def test_load_parent_recursion_error_keeps_r0(tmp_path: Path, monkeypatch: Any) -> None:
    r0, r1 = _case(tmp_path)
    monkeypatch.setattr(
        flow.json, "loads", lambda *args, **_kwargs: (_ for _ in ()).throw(RecursionError())
    )
    _assert_fallback(*_run(r0, r1), "PAUSE_NO_CUT")


def test_first_edit_cut_list_keeps_r0(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    r0 = RunResult(**{**r0.__dict__, "record": {"first_edit_cut": []}})
    _assert_fallback(*_run(r0, r1), "PAUSE_NO_CUT")


def test_record_not_dict_keeps_r0(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    r0 = RunResult(**{**r0.__dict__, "record": []})  # type: ignore[arg-type]
    _assert_fallback(*_run(r0, r1), "PAUSE_NO_CUT")


class _NotJson:
    pass


def test_planner_model_id_not_jsonable_is_sanitized(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    result, rec = _run(
        r0,
        r1,
        planner=Planner(PlanReply("plan", _NotJson(), "low")),  # type: ignore[arg-type]
    )
    _assert_fallback(result, rec, "PAUSE_PLANNER_BAD_OUTPUT")


def test_r1_episode_id_not_jsonable_falls_back(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    r1 = RunResult(**{**r1.__dict__, "episode_id": _NotJson()})  # type: ignore[arg-type]
    result, rec = _run(
        r0, r1, judge=lambda _r: CoreVerdict("VERIFIED", flow._checkout_digest(r1.checkout))
    )
    _assert_fallback(result, rec, "PAUSE_R1_NOT_VERIFIED")


def test_cli_writes_fallback_receipt(tmp_path: Path, monkeypatch: Any) -> None:
    r0, r1 = _case(tmp_path)
    _transcript(r0.transcript, [{"index": 2, "assistant_msg_index": "bad"}])  # type: ignore[arg-type]
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    out = tmp_path / "out"
    out.mkdir()

    def factory(_config: str):
        return (
            Planner(),
            Gate(),
            SETTINGS,
            SETTINGS,
            lambda mode, args, **_kwargs: r0 if mode == "run" else r1,
            None,
        )

    argv = [
        "run",
        "--planner-config",
        "cfg",
        "--task",
        str(task),
        "--checkout",
        str(tmp_path / "checkout"),
        "--out",
        str(out),
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        "c",
    ]
    assert cli.main(argv, run_factory=factory) == 7
    records = (out / cli.RECEIPTS_FILE).read_text().splitlines()
    rec = json.loads(records[-1])
    assert rec["reason"] == "PAUSE_NO_CUT" and receipt.verify(rec) == []


@pytest.mark.parametrize("record", [["x"], "x", 1])
def test_record_truthy_not_dict_keeps_r0(tmp_path: Path, record: Any) -> None:
    r0, r1 = _case(tmp_path)
    r0 = dataclasses.replace(r0, record=record)
    _assert_fallback(*_run(r0, r1), "PAUSE_NO_CUT")


class _BadMetaGate:
    def __init__(self, model_id: Any, effort: Any) -> None:
        self.model_id = model_id
        self.effort = effort

    def review(self, request: Any) -> GateReply:
        return GateReply("ACCEPT", self.model_id, self.effort)  # type: ignore[arg-type]


@pytest.mark.parametrize("model_id,effort", [(123, "low"), ("m", 5)], ids=["model_id", "effort"])
def test_gate_metadata_not_str_falls_back(tmp_path: Path, model_id: Any, effort: Any) -> None:
    r0, r1 = _case(tmp_path)
    result, rec = run_flow(
        [],
        task_id="task",
        planner=Planner(),
        gate=_BadMetaGate(model_id, effort),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=lambda mode, args, **_kwargs: r0 if mode == "run" else r1,
        core_verdict=lambda _r: CoreVerdict("REJECTED", "0" * 64),
    )
    _assert_fallback(result, rec, "PAUSE_GATE_BAD_OUTPUT")


class _BadEffortPlanner:
    def plan(self, request: Any) -> PlanReply:
        return PlanReply("plan", "m", 5)  # type: ignore[arg-type]


def test_planner_effort_not_str_falls_back(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    _assert_fallback(*_run(r0, r1, planner=_BadEffortPlanner()), "PAUSE_PLANNER_BAD_OUTPUT")
