"""R1's repository config must match the runner's post-prepare pin."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply
from experimental.pause.runner import WorkerRunner

SETTINGS = CallSettings("model", "low", 1, 1)


class Planner:
    def plan(self, _request: Any) -> PlanReply:
        return PlanReply("plan", "planner", "low")


class Gate:
    def review(self, _request: Any) -> GateReply:
        return GateReply("ACCEPT", "gate", "low")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _arguments(tmp_path: Path, checkout: Path) -> list[str]:
    task = tmp_path / "task.json"
    task.write_text("{}")
    return [
        "--task",
        str(task),
        "--checkout",
        str(checkout),
        "--out",
        str(tmp_path / "out"),
        "--model",
        "m",
        "--context-cap",
        "100",
        "--canary-report",
        "c",
    ]


def _change_config(checkout: Path, change: str) -> None:
    config = checkout / ".git" / "config"
    if change == "user.name":
        _git(checkout, "config", "user.name", "Changed")
    elif change == "new section":
        config.write_bytes(config.read_bytes() + b"\n[alias]\n\tx = status\n")
    elif change == "deleted key":
        _git(checkout, "config", "--unset", "core.bare")
    elif change == "whitespace":
        data = config.read_bytes()
        config.write_bytes(data.replace(b"bare = false", b"bare  = false"))
    elif change == "comment":
        config.write_bytes(config.read_bytes() + b"\n# changed\n")
    elif change == "symlink":
        copy = checkout / ".git" / "config-copy"
        copy.write_bytes(config.read_bytes())
        config.unlink()
        config.symlink_to(copy.name)


def _worker(tmp_path: Path, mutation: str | None):
    def run(argv: list[str]) -> int:
        mode, *rest = argv
        parsed = __import__("experimental.local_worker.cli", fromlist=["parser"]).parser()
        options = parsed.parse_known_args([mode, *rest])[0]
        checkout, out = options.checkout, options.out
        out.mkdir(parents=True, exist_ok=True)
        eid = "ep_r0" if mode == "run" else "ep_r1"
        if mode == "resume":
            (checkout / "src").mkdir(exist_ok=True)
            (checkout / "src" / "change.py").write_text("changed\n")
            if mutation:
                _change_config(checkout, mutation)
        base = _git(checkout, "rev-parse", "HEAD")
        record: dict[str, Any] = {
            "episode_id": eid,
            "base_sha": base,
            "write_scope": ["src"],
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
            "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 0},
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        corpus = out / "episodes.jsonl"
        corpus.write_text(line)
        private = out / "private" / eid
        private.mkdir(parents=True)
        transcript: dict[str, Any] = {"record": record}
        if mode == "run":
            transcript.update(
                messages=[{"role": "user"}, {"role": "assistant"}],
                turns=[{"index": 0, "assistant_msg_index": 1}],
                write_scope=["src"],
            )
        (private / "transcript.json").write_text(json.dumps(transcript))
        sha = hashlib.sha256(line.encode()).hexdigest()
        print(f"LOCAL_WORKER_EPISODE COMPLETED {eid} {corpus} line_sha256={sha}")
        return 0

    return run


@pytest.mark.parametrize(
    ("mutation", "eligible"),
    [
        ("user.name", False),
        ("new section", False),
        ("deleted key", False),
        ("whitespace", False),
        ("comment", False),
        ("symlink", False),
        ("missing reference", False),
        (None, True),
    ],
    ids=[
        "user-name",
        "new-section",
        "deleted-key",
        "whitespace",
        "comment",
        "symlink",
        "missing-reference",
        "untouched",
    ],
)
def test_config_pin_keeps_r0_unless_config_is_untouched(
    tmp_path: Path, mutation: str | None, eligible: bool
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "Test")
    _git(source, "config", "user.email", "test@example.invalid")
    (source / "src").mkdir()
    (source / "src" / "base.py").write_text("base\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "base")
    r0 = tmp_path / "runs" / "r0" / "checkout"
    r0.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "--quiet", str(source), str(r0)], check=True)
    runner = WorkerRunner(
        _worker(tmp_path, None if mutation == "missing reference" else mutation), tmp_path / "runs"
    )
    typed_runner: Any = cast(Any, runner)
    if mutation == "missing reference":
        original = runner

        def without_pin(mode: str, args: list[str], **kwargs: Any) -> RunResult:
            result = original(mode, args, **kwargs)
            if mode == "resume":
                object.__setattr__(result, "git_config_sha256", None)
            return result

        typed_runner = cast(Any, without_pin)
    result, receipt = run_flow(
        _arguments(tmp_path, r0),
        task_id="task",
        planner=cast(Any, Planner()),
        gate=cast(Any, Gate()),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=typed_runner,
        core_verdict=lambda r1: CoreVerdict("VERIFIED", TreeGuard(r1.checkout).digest()),
    )
    assert receipt["reason"] == (None if eligible else "PAUSE_SCOPE_VIOLATION")
    assert receipt["intervened"] is eligible
    assert result.episode_id == ("ep_r1" if eligible else "ep_r0")
