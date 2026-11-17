"""AC3 probes whether local-worker output distinguishes endpoint failure classes.

Connection failures persist endpoint_unreachable and map to PAUSE_LOCAL_UNREACHABLE; OOM and
other endpoint errors stay endpoint_error and map to PAUSE_R1_INCOMPLETE.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from local_worker_fakes import cli_harness, lines
from pause_fakes import FakeGate, FakePlanner

from cairnwork.workers import agy_dispatch
from experimental.local_worker import cli
from experimental.local_worker.endpoint import (
    EndpointError,
    EndpointTimeout,
    EndpointUnreachable,
)
from experimental.pause import receipt, schema
from experimental.pause.flow import run_flow
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply
from experimental.pause.runner import WorkerRunner

SETTINGS = CallSettings("configured-model", "low", 1, 1)


class FailingEndpoint:
    def __init__(self, _url: str, kind: str) -> None:
        self.url = "http://127.0.0.1:234/v1"
        self.kind = kind

    def models(self, _deadline: float) -> list[str]:
        return [] if self.kind == "model-missing" else ["served-model"]

    def chat(self, _payload: dict[str, Any], _deadline: float) -> dict[str, Any]:
        failures: dict[str, Exception] = {
            "refused": EndpointUnreachable("connection refused"),
            "unreachable": EndpointUnreachable("network is unreachable"),
            "timeout": EndpointTimeout("endpoint timed out"),
            "oom": EndpointError("endpoint answered HTTP 500: out of memory"),
        }
        raise failures[self.kind]


def capture_worker_failure(
    tmp_path: Path, monkeypatch: Any, capsys: Any, kind: str
) -> dict[str, Any]:
    """Run local_worker.main and preserve its printed and corpus output."""
    tmp_path.mkdir(parents=True)
    run, out = cli_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "LoopbackEndpoint", lambda url: FailingEndpoint(url, kind))
    code = run([], "run")
    stdout = capsys.readouterr().out
    episode_line = next(
        (line for line in stdout.splitlines() if line.startswith("LOCAL_WORKER_EPISODE ")),
        None,
    )
    records = lines(out) if list(out.glob("episodes-*.jsonl")) else []
    return {"code": code, "stdout": stdout, "episode_line": episode_line, "records": records}


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo" / "checkout"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Pause test")
    git(repo, "config", "user.email", "pause@example.invalid")
    (repo / "app.py").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def pause_arguments(root: Path, repo: Path) -> list[str]:
    task = root / "task.json"
    task.write_text("{}")
    return [
        "--task",
        str(task),
        "--checkout",
        str(repo),
        "--out",
        str(root / "out"),
        "--model",
        "served-model",
        "--context-cap",
        "65536",
        "--canary-report",
        "x",
    ]


def r0_worker(base: str, out: Path, transcript: Path) -> Any:
    """Stand in for completed R0, as in test_pause_runner.invoke."""

    def worker(argv: list[str]) -> int:
        if argv[0] == "resume":
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
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps(
                {
                    "record": record,
                    "messages": [{"role": "user"}, {"role": "assistant"}],
                    "turns": [{"index": 0, "assistant_msg_index": 1}],
                }
            )
        )
        sha = hashlib.sha256(line.encode()).hexdigest()
        print(f"LOCAL_WORKER_EPISODE COMPLETED ep_r0 {corpus} line_sha256={sha}")
        return 0

    return worker


def replay_outcome(outcome: dict[str, Any]) -> Any:
    """Write layer-one's durable result into the R1 out directory."""

    def worker(argv: list[str]) -> int:
        if argv[0] != "resume":
            return 1
        if not outcome["records"]:
            print(outcome["stdout"], end="")
            return outcome["code"]
        record = dict(outcome["records"][0])
        record["episode_id"] = "ep_r1"
        record["base_sha"] = git(Path(argv[argv.index("--checkout") + 1]), "rev-parse", "HEAD")
        out = Path(argv[argv.index("--out") + 1])
        corpus = out / "episodes.jsonl"
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with corpus.open("a") as stream:
            stream.write(line)
        transcript = out / "private" / "ep_r1" / "transcript.json"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps({"record": record}))
        reason = record["verification"]["exit_reason"]
        sha = hashlib.sha256(line.encode()).hexdigest()
        print(f"LOCAL_WORKER_EPISODE {reason} ep_r1 {corpus} line_sha256={sha}")
        return outcome["code"]

    return worker


@pytest.mark.parametrize(
    ("kind", "worker_code", "exit_reason", "abort_cause", "flow_reason"),
    [
        (
            "refused",
            0,
            "ERROR_ABORT",
            "endpoint_unreachable",
            schema.Reason.PAUSE_LOCAL_UNREACHABLE,
        ),
        (
            "unreachable",
            0,
            "ERROR_ABORT",
            "endpoint_unreachable",
            schema.Reason.PAUSE_LOCAL_UNREACHABLE,
        ),
        ("timeout", 0, "TIMEOUT", None, schema.Reason.PAUSE_R1_INCOMPLETE),
        ("oom", 0, "ERROR_ABORT", "endpoint_error", schema.Reason.PAUSE_R1_INCOMPLETE),
        ("model-missing", 2, None, None, schema.Reason.PAUSE_WORKER_CRASH),
    ],
    ids=["refused", "unreachable", "timeout", "oom", "model-missing"],
)
def test_ac3_local_model_failures(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
    kind: str,
    worker_code: int,
    exit_reason: str | None,
    abort_cause: str | None,
    flow_reason: schema.Reason,
) -> None:
    captured = capture_worker_failure(tmp_path / "local", monkeypatch, capsys, kind)
    assert captured["code"] == worker_code
    if exit_reason is None:
        assert captured["episode_line"] is None and captured["records"] == []
    else:
        [record] = captured["records"]
        assert record["verification"]["exit_reason"] == exit_reason
        assert record["termination"]["abort_cause"] == abort_cause
        assert captured["episode_line"] is not None
        assert f"LOCAL_WORKER_EPISODE {exit_reason} " in captured["episode_line"]

    root = tmp_path / "pause"
    repo, base = make_repo(root)
    args = pause_arguments(root, repo)
    out = root / "out"
    r0_transcript = out / "private" / "ep_r0" / "transcript.json"
    first_worker = r0_worker(base, out, r0_transcript)
    replay = replay_outcome(captured)

    def worker_main(argv: list[str]) -> int:
        return first_worker(argv) if argv[0] == "run" else replay(argv)

    runner = WorkerRunner(worker_main, root / "runs")
    monkeypatch.setattr(
        agy_dispatch, "Dispatch", lambda *_args, **_kwargs: pytest.fail("agy dispatch")
    )
    before = TreeGuard(repo).digest()
    result, receipt_record = run_flow(
        args,
        task_id=f"local-{kind}",
        planner=FakePlanner([PlanReply("safe", "p", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "g", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=runner,
    )
    assert result.episode_id == "ep_r0"
    assert receipt_record["intervened"] is False
    assert receipt_record["reason"] == flow_reason
    assert receipt.verify(receipt_record) == []
    assert TreeGuard(repo).digest() == before
