"""Real WorkerRunner failure rows and AC8 digest-race placement rows."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pause_fakes import FakeGate, FakePlanner
from test_pause_failures_ac8 import TASKS, _write_change

from cairnwork.workers import agy_dispatch
from experimental.pause import receipt, schema
from experimental.pause import runner as pause_runner
from experimental.pause.flow import run_flow
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply
from experimental.pause.runner import WorkerRunner, _prepare

SETTINGS = CallSettings("configured-model", "low", 1, 1)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(root: Path) -> tuple[list[str], Path, str, Path]:
    repo = root / "r0" / "checkout"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Pause test")
    _git(repo, "config", "user.email", "pause@example.invalid")
    (repo / "app.py").write_text("base\n")
    (repo / ".gitignore").write_text("ignored.tmp\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    args = [
        "--task",
        str(root / "task.json"),
        "--checkout",
        str(repo),
        "--out",
        str(root / "out"),
        "--model",
        "m",
        "--context-cap",
        "100",
        "--canary-report",
        "c",
    ]
    Path(args[args.index("--task") + 1]).write_text("{}")
    return args, repo, base, root / "out"


def _r0_worker(base: str, out: Path, transcript: Path, behavior: str = "ok"):
    """Write a strict runner-parseable R0 episode and parent transcript."""

    def worker(argv: list[str]) -> int:
        if argv[0] == "resume":
            if behavior == "raises":
                raise RuntimeError("worker crashed")
            if behavior == "nonzero":
                return 9
            if behavior == "malformed":
                print("LOCAL_WORKER_EPISODE malformed")
                return 0
            if behavior == "missing-transcript":
                (out / "private" / "ep_r1" / "transcript.json").unlink(missing_ok=True)
            if behavior == "killed":
                return -9
            return 1
        record = {
            "episode_id": "ep_r0",
            "base_sha": base,
            "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 0},
            "verification": {"exit_reason": "COMPLETED"},
        }
        corpus = out / "episodes.jsonl"
        corpus.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        corpus.write_text(line)
        private = out / "private" / "ep_r0"
        private.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "record": record,
                    "messages": [{"role": "user"}, {"role": "assistant"}],
                    "turns": [{"index": 0, "assistant_msg_index": 1}],
                    "write_scope": ["src"],
                }
            )
        )
        (private / "transcript.json").write_text(transcript.read_text())
        sha = hashlib.sha256(line.encode()).hexdigest()
        print(f"LOCAL_WORKER_EPISODE COMPLETED ep_r0 {corpus} line_sha256={sha}")
        return 0

    return worker


@pytest.mark.parametrize(
    "failure", ["raises", "nonzero", "malformed", "missing-transcript", "killed"]
)
def test_ac3_real_runner_worker_crash_rows(tmp_path: Path, monkeypatch: Any, failure: str) -> None:
    args, repo, base, out = _setup(tmp_path)
    transcript = out / "private" / "ep_r0" / "transcript.json"
    runner = WorkerRunner(_r0_worker(base, out, transcript, failure), tmp_path / "runs")
    monkeypatch.setattr(agy_dispatch, "Dispatch", lambda *_a, **_k: pytest.fail("agy dispatch"))
    result, rec = run_flow(
        args,
        task_id="crash",
        planner=FakePlanner([PlanReply("safe", "p", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "g", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result.episode_id == "ep_r0"
    assert rec["reason"] == schema.Reason.PAUSE_WORKER_CRASH and not rec["intervened"]
    assert receipt.verify(rec) == []


def test_real_runner_rejects_dirty_prepared_checkout_before_worker(
    tmp_path: Path, monkeypatch: Any
) -> None:
    args, _repo, base, out = _setup(tmp_path)
    transcript = out / "private" / "ep_r0" / "transcript.json"
    worker = _r0_worker(base, out, transcript)
    dispatched: list[str] = []

    def dirty_prepare(source: Path, checkout: Path, pinned_base: str) -> None:
        original_git = pause_runner._git

        def dirty_status(path: Path, *git_args: str) -> str:
            if path == checkout and git_args[:1] == ("status",):
                (checkout / "dirty.txt").write_text("unexpected residue\n")
            return original_git(path, *git_args)

        pause_runner._git = dirty_status
        try:
            _prepare(source, checkout, pinned_base)
        finally:
            pause_runner._git = original_git

    def tracked(argv: list[str]) -> int:
        if argv[0] == "resume":
            dispatched.append("resume")
        return worker(argv)

    runner = WorkerRunner(tracked, tmp_path / "runs", prepare=dirty_prepare)
    monkeypatch.setattr(agy_dispatch, "Dispatch", lambda *_a, **_k: pytest.fail("agy dispatch"))
    result, rec = run_flow(
        args,
        task_id="dirty-prepared-checkout",
        planner=FakePlanner([PlanReply("safe", "p", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "g", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result.episode_id == "ep_r0"
    assert rec["reason"] == schema.Reason.PAUSE_WORKER_CRASH
    assert receipt.verify(rec) == []
    assert dispatched == []


@pytest.mark.parametrize("task", TASKS)
def test_ac8_real_runner_change_between_digests(
    tmp_path: Path, monkeypatch: Any, task: str
) -> None:
    args, repo, base, out = _setup(tmp_path)
    transcript = out / "private" / "ep_r0" / "transcript.json"
    worker = _r0_worker(base, out, transcript)

    class InjectingGuard:
        count = 0

        def __init__(self, path: Path, **options) -> None:
            self.guard = TreeGuard(path, **options)
            self.path = path

        def digest(self) -> str:
            type(self).count += 1
            if type(self).count == 2:
                _write_change(self.path, task)
            return self.guard.digest()

    monkeypatch.setattr("experimental.pause.runner.TreeGuard", InjectingGuard)
    resume_calls: list[bool] = []

    def tracked(argv: list[str]) -> int:
        if argv[0] == "resume":
            resume_calls.append(True)
        return worker(argv)

    runner = WorkerRunner(tracked, tmp_path / "runs")
    monkeypatch.setattr(agy_dispatch, "Dispatch", lambda *_a, **_k: pytest.fail("agy dispatch"))
    result, rec = run_flow(
        args,
        task_id=task,
        planner=FakePlanner([PlanReply("safe", "p", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "g", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert not resume_calls
    assert result.episode_id == "ep_r0"
    assert rec["reason"] == schema.Reason.PAUSE_GUARD_UNAVAILABLE and not rec["intervened"]
    assert receipt.verify(rec) == []


def test_ac8_real_runner_time_only_change_between_digests_stops_the_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Same-tree before/arrival digests bind mtime and ctime (runner bind_times=True): a change
    that only moves app.py's mtime must still stop the run, and the run must not resume."""
    args, repo, base, out = _setup(tmp_path)
    transcript = out / "private" / "ep_r0" / "transcript.json"
    worker = _r0_worker(base, out, transcript)

    class TouchingGuard:
        count = 0

        def __init__(self, path: Path, **options) -> None:
            self.guard = TreeGuard(path, **options)
            self.path = path

        def digest(self) -> str:
            type(self).count += 1
            if type(self).count == 2:
                os.utime(self.path / "app.py", ns=(1_000_000_000, 1_000_000_000))
            return self.guard.digest()

    monkeypatch.setattr("experimental.pause.runner.TreeGuard", TouchingGuard)
    resume_calls: list[bool] = []

    def tracked(argv: list[str]) -> int:
        if argv[0] == "resume":
            resume_calls.append(True)
        return worker(argv)

    runner = WorkerRunner(tracked, tmp_path / "runs")
    monkeypatch.setattr(agy_dispatch, "Dispatch", lambda *_a, **_k: pytest.fail("agy dispatch"))
    result, rec = run_flow(
        args,
        task_id="time-only",
        planner=FakePlanner([PlanReply("safe", "p", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "g", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert not resume_calls
    assert rec["reason"] == schema.Reason.PAUSE_GUARD_UNAVAILABLE and not rec["intervened"]
    assert receipt.verify(rec) == []
