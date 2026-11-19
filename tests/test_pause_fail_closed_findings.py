"""Fail-closed branch coverage from the #1305 review."""

import json
from dataclasses import replace

import pytest
from test_pause_flow import SETTINGS, _scenario

from experimental.pause.flow import CoreVerdict, run_flow
from experimental.pause.interfaces import GateReply, PlanReply


def _flow(tmp_path, monkeypatch, *, planner=None, gate=None, update=None, judge=None):
    runner, calls, r0, r1, planner_obj, gate_obj = _scenario(
        tmp_path, monkeypatch, planner=planner, gate=gate
    )
    if update:
        r0, r1 = update(r0, r1)

    def actual_runner(mode, args, **kwargs):
        calls.append((mode, kwargs))
        return r0 if mode == "run" else r1

    result, rec = run_flow(
        [],
        task_id="task",
        planner=planner_obj,
        gate=gate_obj,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=actual_runner,
        core_verdict=judge,
    )
    return result, rec, r0, r1, planner_obj, gate_obj, calls


@pytest.mark.parametrize("same", ["root", "checkout"])
def test_same_r0_r1_identity_is_scope_violation(tmp_path, monkeypatch, same):
    def reuse(r0, r1):
        return r0, replace(r1, **{same: getattr(r0, same)})

    result, rec, r0, _, _, _, _ = _flow(tmp_path, monkeypatch, update=reuse)
    assert result is r0 and rec["reason"] == "PAUSE_SCOPE_VIOLATION"


def test_planner_none_text_is_bad_output(tmp_path, monkeypatch):
    result, rec, r0, _, _, _, calls = _flow(
        tmp_path, monkeypatch, planner=PlanReply(None, "p", "low")
    )
    assert result is r0 and rec["reason"] == "PAUSE_PLANNER_BAD_OUTPUT"
    assert all(mode != "resume" for mode, _ in calls)


@pytest.mark.parametrize(
    "verdict,reason",
    [("MAYBE", "PAUSE_GATE_BAD_OUTPUT"), ("REJECT", "PAUSE_GATE_REJECT_LIMIT")],
)
def test_gate_fails_closed_without_resume(tmp_path, monkeypatch, verdict, reason):
    result, rec, r0, _, _, _, calls = _flow(
        tmp_path, monkeypatch, gate=GateReply(verdict, "g", "low")
    )
    assert result is r0 and rec["reason"] == reason
    assert all(mode != "resume" for mode, _ in calls)


def test_bad_transcript_keeps_r0_with_no_cut_receipt(tmp_path, monkeypatch):
    runner, _, r0, _, planner, gate = _scenario(tmp_path, monkeypatch)
    assert r0.transcript is not None
    r0.transcript.write_text(json.dumps({"messages": [], "wrong": []}))
    result, rec = run_flow(
        [],
        task_id="task",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result is r0 and rec["reason"] == "PAUSE_NO_CUT"


def test_transcript_parse_error_keeps_r0_with_no_cut_receipt(tmp_path, monkeypatch):
    runner, _, r0, _, planner, gate = _scenario(tmp_path, monkeypatch)
    assert r0.transcript is not None
    r0.transcript.write_text("{")
    result, rec = run_flow(
        [],
        task_id="task",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result is r0 and rec["reason"] == "PAUSE_NO_CUT"


def test_real_treeguard_digest_binds_verdict(tmp_path, monkeypatch):
    from experimental.pause import flow

    real_digest = flow._checkout_digest
    runner, _, r0, r1, planner, gate = _scenario(tmp_path, monkeypatch)
    monkeypatch.setattr(flow, "_checkout_digest", real_digest)
    r1.checkout.mkdir(parents=True)
    (r1.checkout / "file.txt").write_text("content")
    digest = real_digest(r1.checkout)
    result, rec = run_flow(
        [],
        task_id="task",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
        core_verdict=lambda _: CoreVerdict("VERIFIED", digest),
    )
    assert result is r1 and rec["core_tree_sha256"] == digest


@pytest.mark.parametrize("scope", ["src", {}, ["src", 5]])
def test_malformed_write_scope_is_scope_violation(tmp_path, monkeypatch, scope):
    runner, _, r0, r1, planner, gate = _scenario(tmp_path, monkeypatch)
    assert r0.transcript is not None
    payload = json.loads(r0.transcript.read_text())
    payload["write_scope"] = scope
    r0.transcript.write_text(json.dumps(payload))
    result, rec = run_flow(
        [],
        task_id="task",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result is r0 and rec["reason"] == "PAUSE_SCOPE_VIOLATION"
