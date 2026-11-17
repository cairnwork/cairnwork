"""Offline acceptance for the post-hoc R0/R1 PAUSE flow."""

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply


class Planner:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def plan(self, request):
        self.calls.append(request)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class Gate:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def review(self, request):
        self.calls.append(request)
        return self.reply


def _scenario(tmp_path, monkeypatch, cut="CUT", planner=None, gate=None, scope=True, guard=True):
    t0 = tmp_path / "transcript.json"
    t0.write_text(
        json.dumps(
            {
                "messages": [{"role": "user", "content": "task"}],
                "turns": [{"index": 2, "assistant_msg_index": 1}],
                "write_scope": ["src"],
            }
        )
    )
    record = {
        "first_edit_cut": {"cut_status": cut, "cut_turn_index": 2 if cut == "CUT" else None},
        "base_sha": "b" * 40,
    }
    r0 = RunResult(0, "R0 output\n", tmp_path / "R0", tmp_path / "R0" / "checkout", t0, record)
    r1 = RunResult(
        0,
        "R1 output\n",
        tmp_path / "R1",
        tmp_path / "R1" / "checkout",
        guard_before_plan="digest",
        guard_at_plan_arrival="digest" if guard else "changed",
        git_config_sha256="0" * 64,
        record={
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
        },
    )
    calls = []

    def runner(mode, args, **kw):
        calls.append((mode, kw))
        return r0 if mode == "run" else r1

    monkeypatch.setattr("experimental.pause.flow._checkout_digest", lambda path: "a" * 64)
    monkeypatch.setattr(
        "experimental.pause.flow._scope_ok", lambda path, _, base, _config_pin: scope
    )
    return (
        runner,
        calls,
        r0,
        r1,
        Planner(planner or PlanReply("plan", "p-model", "low")),
        Gate(gate or GateReply("ACCEPT", "g-model", "medium")),
    )


SETTINGS = CallSettings("model", "low", 1.0, 1)


def test_unobserved_returns_r0_and_never_plans(tmp_path, monkeypatch):
    runner, calls, r0, _, planner, gate = _scenario(tmp_path, monkeypatch, cut="UNOBSERVED")
    result, receipt = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result is r0 and receipt["reason"] == "PAUSE_NO_CUT"
    assert calls == [("run", {})] and not planner.calls and not gate.calls


@pytest.mark.parametrize(
    "gate_reply,planner_reply,expected",
    [
        (
            GateReply("REJECT", "g", "medium"),
            PlanReply("plan", "p", "low"),
            "PAUSE_GATE_REJECT_LIMIT",
        ),
        (
            GateReply("ACCEPT", "g", "medium"),
            RuntimeError("planner offline"),
            "PAUSE_PLANNER_TIMEOUT",
        ),
    ],
)
def test_reject_or_fallback_returns_r0(tmp_path, monkeypatch, gate_reply, planner_reply, expected):
    runner, calls, r0, _, planner, gate = _scenario(
        tmp_path, monkeypatch, planner=planner_reply, gate=gate_reply
    )
    result, receipt = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
        core_verdict=lambda _: CoreVerdict("VERIFIED", "a" * 64),
    )
    assert result is r0 and receipt["reason"] == expected
    assert all(mode != "resume" for mode, _ in calls)


def test_accept_uses_fresh_r1_and_binds_receipt(tmp_path, monkeypatch):
    runner, calls, _, r1, planner, gate = _scenario(tmp_path, monkeypatch)
    result, receipt = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
        core_verdict=lambda _: CoreVerdict("VERIFIED", "a" * 64),
    )
    assert result is r1 and [mode for mode, _ in calls] == ["run", "resume"]
    assert calls[1][1]["cut"] == 2 and calls[1][1]["plan"] == "plan"
    assert receipt["intervened"] is True and receipt["gate_verdict"] == "ACCEPT"
    assert receipt["checkpoint_sha256"] and receipt["plan_sha256"]


def test_flow_keeps_r0_when_core_copy_differs_from_r1_entry_tree(tmp_path, monkeypatch):
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    _runner, _calls, r0, r1, planner, gate = _scenario(flow_dir, monkeypatch)
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
    r1 = replace(r1, checkout=checkout, base_sha=base)
    monkeypatch.setattr("experimental.pause.flow._scope_ok", lambda *_args: True)
    monkeypatch.setattr(
        "experimental.pause.flow._checkout_digest", lambda path: TreeGuard(path).digest()
    )

    from experimental.pause import core_judge

    original_clone = core_judge._clone
    original_candidate_digest = core_judge._candidate_tree_digest

    def clone_with_transient_change(source: Path, destination: Path) -> None:
        (checkout / "a").write_text("B\n")
        original_clone(source, destination)

    seen: list[str] = []

    def verify(_intake: Any, **kwargs: Any) -> dict[str, str]:
        seen.append(
            subprocess.check_output(
                ["git", "-C", str(kwargs["repo"]), "show", f"{kwargs['candidate']}:a"],
                text=True,
            ).strip()
        )
        return {"status": "VERIFIED"}

    def candidate_digest_then_restore(
        candidate_repo: Path, candidate: str, excluded: frozenset[str]
    ) -> str:
        digest = original_candidate_digest(candidate_repo, candidate, excluded)
        (checkout / "a").write_text("A\n")
        return digest

    monkeypatch.setattr(core_judge, "_clone", clone_with_transient_change)
    monkeypatch.setattr(core_judge, "_candidate_tree_digest", candidate_digest_then_restore)
    judge = CoreJudge(tmp_path / "state", (), verify=verify)
    try:
        result, receipt = run_flow(
            [],
            task_id="t",
            planner=planner,
            gate=gate,
            planner_settings=SETTINGS,
            gate_settings=SETTINGS,
            runner=lambda mode, args, **_kwargs: r0 if mode == "run" else r1,
            core_verdict=judge,
        )
    finally:
        (checkout / "a").write_text("A\n")
    assert result is r0
    assert receipt["reason"] == "PAUSE_R1_NOT_VERIFIED"
    assert seen == []


@pytest.mark.parametrize(
    "scope,guard,reason",
    [
        (False, True, "PAUSE_SCOPE_VIOLATION"),
        (True, False, "PAUSE_GUARD_UNAVAILABLE"),
    ],
)
def test_bad_r1_is_discarded_and_r0_wins(tmp_path, monkeypatch, scope, guard, reason):
    runner, _, r0, _, planner, gate = _scenario(tmp_path, monkeypatch, scope=scope, guard=guard)
    result, receipt = run_flow(
        [],
        task_id="t",
        planner=planner,
        gate=gate,
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result is r0 and receipt["reason"] == reason and receipt["intervened"] is False


def _git(checkout, *args):
    subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True)


@pytest.mark.parametrize("violation", ["outside", "symlink", "mode"])
def test_real_scope_check_rejects_unsafe_r1_and_keeps_r0(tmp_path, monkeypatch, violation):
    from experimental.pause.flow import _scope_ok

    real_scope_ok = _scope_ok

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.email", "test@example.invalid")
    _git(checkout, "config", "user.name", "Test")
    (checkout / "src").mkdir()
    (checkout / "src" / "file.txt").write_text("original")
    _git(checkout, "add", "src/file.txt")
    _git(checkout, "commit", "-qm", "base")
    if violation == "outside":
        (checkout / "other.txt").write_text("outside scope")
    elif violation == "symlink":
        (checkout / "src" / "link").symlink_to("file.txt")
    else:
        (checkout / "src" / "file.txt").chmod(0o755)
    if violation == "mode":
        assert _scope_ok(
            checkout,
            ["src"],
            subprocess.check_output(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
            ).strip(),
            hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest(),
        )
        return
    base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    assert not _scope_ok(checkout, ["src"], base)

    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    _, _, r0, r1, planner, gate = _scenario(flow_dir, monkeypatch)
    monkeypatch.setattr("experimental.pause.flow._scope_ok", real_scope_ok)
    r1 = replace(r1, checkout=checkout)

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
    )
    assert result is r0
    assert rec["intervened"] is False and rec["reason"] == "PAUSE_SCOPE_VIOLATION"
