"""#1356 winner controls: reverting the code-zero shortcut makes abort and unverified cases red."""

from dataclasses import replace

import pytest
from test_pause_flow import SETTINGS, _scenario

from experimental.pause import receipt
from experimental.pause.flow import run_flow


def _run(tmp_path, monkeypatch, record, judge=None):
    runner, _, r0, original_r1, planner, gate = _scenario(tmp_path, monkeypatch)
    r1 = replace(original_r1, record=record)

    def actual_runner(mode, args, **kwargs):
        return r0 if mode == "run" else r1

    result, rec = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=actual_runner,
        core_verdict=judge,
    )
    return result, rec, r0, r1


def _record(exit_reason, *, needs_spec=False):
    return {
        "verification": {"exit_reason": exit_reason},
        "spec_feedback": {"needs_spec": needs_spec},
    }


def _assert_fallback(result, rec, r0, reason):
    assert result is r0
    assert rec["intervened"] is False
    assert rec["reason"] == reason
    assert receipt.verify(rec) == []


@pytest.mark.parametrize("exit_reason", ["ERROR_ABORT", "REPEAT_LIMIT"])
def test_completed_exit_required_before_judge(tmp_path, monkeypatch, exit_reason):
    calls = []

    def judge(r1):
        calls.append(r1)
        return "VERIFIED"

    result, rec, r0, _ = _run(tmp_path, monkeypatch, _record(exit_reason), judge)
    _assert_fallback(result, rec, r0, "PAUSE_R1_INCOMPLETE")
    assert calls == []


@pytest.mark.parametrize(
    "record",
    [
        _record("TIMEOUT"),
        None,
        {"spec_feedback": {"needs_spec": False}},
        _record("COMPLETED", needs_spec=True),
    ],
)
def test_incomplete_or_needs_spec_does_not_win(tmp_path, monkeypatch, record):
    calls = []
    result, rec, r0, _ = _run(tmp_path, monkeypatch, record, lambda r: calls.append(r))
    _assert_fallback(result, rec, r0, "PAUSE_R1_INCOMPLETE")
    assert calls == []


@pytest.mark.parametrize(
    "verdict",
    ["FAILED", "REFUSED", "UNCOMPARABLE", "verified", "VERIFIED ", True, None],
)
def test_only_exact_verified_core_verdict_wins(tmp_path, monkeypatch, verdict):
    result, rec, r0, _ = _run(tmp_path, monkeypatch, _record("COMPLETED"), lambda _: verdict)
    _assert_fallback(result, rec, r0, "PAUSE_R1_NOT_VERIFIED")


def test_missing_judge_fails_closed(tmp_path, monkeypatch):
    result, rec, r0, _ = _run(tmp_path, monkeypatch, _record("COMPLETED"))
    _assert_fallback(result, rec, r0, "PAUSE_R1_NOT_VERIFIED")


def test_judge_error_fails_closed(tmp_path, monkeypatch):
    def judge(_):
        raise RuntimeError("Core unavailable")

    result, rec, r0, _ = _run(tmp_path, monkeypatch, _record("COMPLETED"), judge)
    _assert_fallback(result, rec, r0, "PAUSE_R1_NOT_VERIFIED")


def test_verified_completed_r1_wins_and_judge_receives_r1(tmp_path, monkeypatch):
    calls = []

    def judge(r1):
        calls.append(r1)
        return "VERIFIED"

    result, rec, _, r1 = _run(tmp_path, monkeypatch, _record("COMPLETED"), judge)
    assert result is r1
    assert rec["intervened"] is True and rec["reason"] is None
    assert calls == [r1]
    assert receipt.verify(rec) == []
