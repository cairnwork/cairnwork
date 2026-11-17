"""Additional AC4 failure rows for PAUSE's reject and secret boundaries."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from pause_fakes import FakeGate, FakePlanner

from experimental.pause import receipt, schema
from experimental.pause.client import HaikuGate, HaikuPlanner, TransportResult
from experimental.pause.flow import RunResult, run_flow
from experimental.pause.interfaces import (
    CallSettings,
    GateReply,
    GateRequest,
    PlanReply,
    PlanRequest,
)
from experimental.pause.secrets_scan import Scanner

SETTINGS = CallSettings("configured-model", "low", 1, 1)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _world(root: Path) -> tuple[list[str], RunResult, str]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Pause test")
    _git(repo, "config", "user.email", "pause@example.invalid")
    (repo / "app.py").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    transcript = root / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "messages": [{"role": "user"}, {"role": "assistant"}],
                "turns": [{"index": 0, "assistant_msg_index": 1}],
                "write_scope": ["src"],
            }
        )
    )
    r0 = RunResult(
        0,
        "r0",
        root,
        repo,
        transcript,
        {"base_sha": base, "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 0}},
        "ep_r0",
    )
    return ["--checkout", str(repo), "--out", str(root / "out")], r0, base


@pytest.mark.parametrize("rejects", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
def test_reject_never_resumes_and_preserves_r0(tmp_path: Path, rejects: int) -> None:
    args, r0, _ = _world(tmp_path)
    before = (r0.checkout / "app.py").read_bytes()
    calls: list[str] = []

    def runner(mode: str, _args: list[str], **_kwargs: Any) -> RunResult:
        calls.append(mode)
        return r0

    result, rec = run_flow(
        args,
        task_id="reject",
        planner=FakePlanner([PlanReply("plan", "configured-model", "low")]),
        gate=FakeGate([GateReply("REJECT", "configured-model", "low") for _ in range(rejects)]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, runner),
    )
    assert result is r0 and calls == ["run"]
    assert rec["reason"] == schema.Reason.PAUSE_GATE_REJECT_LIMIT
    assert rec["intervened"] is False and receipt.verify(rec) == []
    assert (r0.checkout / "app.py").read_bytes() == before


class _Transport:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def send(self, model: str, effort: str, messages: Any, timeout: float) -> TransportResult:
        self.calls.append((model, effort, messages, timeout))
        return TransportResult(200, {"text": "safe"})


@pytest.mark.parametrize(
    "secret",
    [
        "sk-fake-12345678901234567890",
        "ghp_FAKE123456789012345678901234567890",
        "AKIA1234567890ABCDEF",
        "/Users/fake-account/private/repo",
        "fake-host.internal",
        "jane.fake@example.invalid",
        "xoxb-FAKE-1234567890",
        "-----BEGIN PRIVATE KEY-----",
        "github_pat_FAKE12345678901234567890",
        "api_key = FAKE1234567890123456",
    ],
)
def test_secret_outgoing_requests_are_blocked_before_transport(secret: str) -> None:
    planner_transport = _Transport()
    scanner = Scanner(("fake-host.internal", "jane.fake"))
    planner = HaikuPlanner(planner_transport, scanner)
    plan = planner.plan(
        PlanRequest("t", "a" * 64, ({"role": "user", "content": secret},), SETTINGS)
    )
    assert plan.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert planner_transport.calls == []

    gate_transport = _Transport()
    gate = HaikuGate(gate_transport, scanner)
    verdict = gate.review(GateRequest("t", "a" * 64, secret, SETTINGS))
    assert verdict.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert gate_transport.calls == []
