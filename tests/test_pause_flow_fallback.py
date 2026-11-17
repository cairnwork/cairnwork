"""R0 receipts remain available when accepted planner replies cannot be recorded."""

import json
from dataclasses import replace
from typing import Any

import pytest
from test_pause_flow import SETTINGS, _scenario

from experimental.pause import cli, receipt, schema
from experimental.pause.client import HaikuPlanner, TransportResult
from experimental.pause.flow import CoreVerdict, run_flow
from experimental.pause.interfaces import GateReply, PlanReply


def check(result, r0, rec, reason):
    assert result is r0
    assert rec["intervened"] is False
    assert rec["reason"] == reason
    assert receipt.verify(rec) == []


@pytest.mark.parametrize(
    "reply_reason",
    [schema.Reason.PAUSE_GUARD_ROLLED_BACK, str(schema.Reason.PAUSE_GUARD_ROLLED_BACK)],
)
def test_planner_trigger_reason_returns_bad_output(tmp_path, monkeypatch, reply_reason):
    runner, _, r0, _, planner, gate = _scenario(
        tmp_path, monkeypatch, planner=PlanReply("plan", "p", "low", reply_reason)
    )
    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    check(result, r0, rec, "PAUSE_PLANNER_BAD_OUTPUT")


@pytest.mark.parametrize(
    "reply_reason",
    [schema.Reason.PAUSE_GUARD_ROLLED_BACK, str(schema.Reason.PAUSE_GUARD_ROLLED_BACK)],
)
def test_gate_trigger_reason_returns_bad_output(tmp_path, monkeypatch, reply_reason):
    runner, _, r0, _, planner, gate = _scenario(
        tmp_path, monkeypatch, gate=GateReply("ACCEPT", "g", "medium", reply_reason)
    )
    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    check(result, r0, rec, "PAUSE_GATE_BAD_OUTPUT")


@pytest.mark.parametrize("accept", [False, True])
def test_unencodable_plan_stops_before_gate_or_resume(tmp_path, monkeypatch, accept):
    from test_pause_flow import Gate, Planner

    runner, calls, r0, _, _, _ = _scenario(tmp_path, monkeypatch)
    planner = Planner(PlanReply("fix \ud800 now", "p", "low"))
    gate = Gate(GateReply("ACCEPT" if accept else "REJECT", "g", "medium"))
    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    check(result, r0, rec, "PAUSE_PLANNER_BAD_OUTPUT")
    assert not gate.calls and all(mode != "resume" for mode, _ in calls)


def test_haiku_plan_with_surrogate_escape_fails_closed(tmp_path, monkeypatch):
    class Transport:
        def send(self, *_args):
            body = json.loads('{"text":"fix \\ud800 now"}')
            return TransportResult(200, body)

    runner, _, r0, _, _, gate = _scenario(tmp_path, monkeypatch)
    transport: Any = Transport()
    planner = HaikuPlanner(transport)
    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    check(result, r0, rec, "PAUSE_PLANNER_BAD_OUTPUT")
    assert not gate.calls


def test_non_path_transcript_returns_no_cut(tmp_path, monkeypatch):
    runner, _, r0, _, planner, gate = _scenario(tmp_path, monkeypatch)
    r0 = replace(r0, transcript="not-a-path")

    def run(mode, args, **kwargs):
        return r0

    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=run,
    )
    check(result, r0, rec, "PAUSE_NO_CUT")


def test_bad_fallback_build_retries_with_empty_r0_data(tmp_path, monkeypatch):
    runner, _, r0, _, planner, gate = _scenario(
        tmp_path,
        monkeypatch,
        planner=PlanReply("plan", "p", "low", schema.Reason.PAUSE_GUARD_ROLLED_BACK),
    )
    original = receipt.build
    calls = []

    def flaky(**kwargs):
        if kwargs["intervened"] is False and not calls:
            calls.append(kwargs)
            raise ValueError("bad optional info")
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr("experimental.pause.flow.receipt.build", flaky)
    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
        event_seq=lambda _: 7,
    )
    check(result, r0, rec, "PAUSE_PLANNER_BAD_OUTPUT")
    assert calls[0]["event_seq"] == calls[1]["event_seq"] == 7
    assert all(
        rec[key] is None
        for key in schema.RECEIPT_FIELDS
        if key
        in (
            "plan_sha256",
            "planner_model_id",
            "planner_effort",
            "gate_model_id",
            "gate_effort",
            "gate_verdict",
            "core_verdict",
            "core_tree_sha256",
        )
    )


def test_verified_r1_still_wins(tmp_path, monkeypatch):
    runner, _, _, r1, planner, gate = _scenario(tmp_path, monkeypatch)
    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
        core_verdict=lambda _: CoreVerdict("VERIFIED", "a" * 64),
    )
    assert result is r1 and rec["intervened"] is True
    assert receipt.verify(rec) == []


def test_cli_writes_valid_r0_receipt_for_trigger_reason(tmp_path, monkeypatch, capsys):
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"t"}')
    out = tmp_path / "out"
    out.mkdir()
    runner, _, r0, _, planner, gate = _scenario(
        tmp_path,
        monkeypatch,
        planner=PlanReply("plan", "p", "low", schema.Reason.PAUSE_GUARD_ROLLED_BACK),
    )

    def factory(_):
        return planner, gate, SETTINGS, SETTINGS, runner, None

    monkeypatch.setattr(cli.receipt_module, "next_event_seq", lambda _: 0)
    code = cli.main(
        [
            "run",
            "--planner-config",
            "cfg",
            "--task",
            str(task),
            "--checkout",
            "checkout",
            "--model",
            "m",
            "--context-cap",
            "1",
            "--canary-report",
            "x",
            "--out",
            str(out),
        ],
        run_factory=factory,
    )
    assert code == r0.code
    lines = (out / schema.RECEIPTS_FILE).read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    check(r0, r0, rec, "PAUSE_PLANNER_BAD_OUTPUT")
