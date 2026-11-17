"""Flow-level regressions for the Core source-tree binding."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult, run_flow
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply


class Planner:
    def plan(self, _request):
        return PlanReply("plan", "p-model", "low")


class Gate:
    def review(self, _request):
        return GateReply("ACCEPT", "g-model", "medium")


SETTINGS = CallSettings("model", "low", 1.0, 1)


def _scenario(tmp_path: Path) -> tuple[RunResult, RunResult, Planner, Gate]:
    transcript = tmp_path / "transcript.json"
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
        0,
        "R0 output\n",
        tmp_path / "R0",
        tmp_path / "R0" / "checkout",
        transcript,
        {
            "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 2},
            "base_sha": "b" * 40,
        },
    )
    r1 = RunResult(
        0,
        "R1 output\n",
        tmp_path / "R1",
        tmp_path / "R1" / "checkout",
        guard_before_plan="digest",
        guard_at_plan_arrival="digest",
        git_config_sha256="0" * 64,
        record={
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
        },
    )
    return r0, r1, Planner(), Gate()


def _git(checkout: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True)


def _checkout(tmp_path: Path, r0: RunResult) -> tuple[RunResult, Path, str]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.name", "Test")
    _git(checkout, "config", "user.email", "test@example.invalid")
    (checkout / "a").write_text("base\n")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "base")
    base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    (checkout / "a").write_text("A\n")
    r0 = replace(r0, record={**(r0.record or {}), "base_sha": base})
    return r0, checkout, base


@pytest.mark.parametrize("change_point", ["before-copy-snapshot", "before-judge-entry"])
def test_flow_keeps_r0_when_source_changes_after_flow_digest(tmp_path, monkeypatch, change_point):
    r0, r1, planner, gate = _scenario(tmp_path)
    r0, checkout, base = _checkout(tmp_path, r0)
    r1 = replace(r1, checkout=checkout, base_sha=base)
    monkeypatch.setattr("experimental.pause.flow._scope_ok", lambda *_args: True)
    monkeypatch.setattr(
        "experimental.pause.flow._checkout_digest", lambda path: TreeGuard(path).digest()
    )

    verify_calls = []

    def verify(_intake: Any, **_kwargs: Any) -> dict[str, str]:
        verify_calls.append(True)
        if change_point == "before-copy-snapshot":
            (checkout / "a").write_text("A\n")
        return {"status": "VERIFIED"}

    judge = CoreJudge(tmp_path / "state", (), verify=verify)

    if change_point == "before-copy-snapshot":
        from experimental.pause import core_judge

        original_tree_entries = core_judge._tree_entries

        def tree_entries_then_change(path: Path, candidate: str):
            if path == checkout:
                (checkout / "a").write_text("B\n")
            return original_tree_entries(path, candidate)

        monkeypatch.setattr(core_judge, "_tree_entries", tree_entries_then_change)
        core_verdict = judge
    else:

        def change_then_judge(item):
            (checkout / "a").write_text("B\n")
            return judge(item)

        core_verdict = change_then_judge

    result, receipt = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=lambda mode, _args, **_kwargs: r0 if mode == "run" else r1,
        core_verdict=core_verdict,
    )
    assert result is r0
    assert receipt["reason"] == "PAUSE_R1_NOT_VERIFIED"
    assert verify_calls == [True]
    assert (checkout / "a").read_text() == (
        "A\n" if change_point == "before-copy-snapshot" else "B\n"
    )
