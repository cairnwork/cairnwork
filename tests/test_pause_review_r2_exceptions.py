"""Fail-closed regressions from the PAUSE/Core independent review."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from cairnwork.gitcmd import GitCommandError
from cairnwork.intake_request import IntakeRefused
from cairnwork.pipeline import TaskRejected
from cairnwork.workspace import WorkspaceError
from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply
from experimental.pause.scope import _scope_ok

SETTINGS = CallSettings("m", "low", 1.0, 1)


class Planner:
    def plan(self, _request: Any) -> PlanReply:
        return PlanReply("plan", "p", "low")


class Gate:
    def review(self, _request: Any) -> GateReply:
        return GateReply("ACCEPT", "g", "low")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _case(tmp_path: Path, mutation: str = "edit") -> tuple[RunResult, RunResult, str]:
    checkout = tmp_path / "R1" / "checkout"
    checkout.mkdir(parents=True)
    _git(checkout, "init", "-q")
    (checkout / "src").mkdir()
    (checkout / "src/a.py").write_text("base\n")
    if mutation == "chmod_minus":
        (checkout / "src/a.py").chmod(0o755)
    (checkout / ".gitignore").write_text("*.log\n")
    _git(checkout, "add", ".")
    _git(checkout, "-c", "user.name=test", "-c", "user.email=t@invalid", "commit", "-qm", "base")
    base = _git(checkout, "rev-parse", "HEAD")
    _apply_mutation(checkout, tmp_path, mutation, base)
    transcript = tmp_path / "parent.json"
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
        7,
        "R0 output\n",
        tmp_path / "R0",
        tmp_path / "R0/checkout",
        transcript,
        {"first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 2}, "base_sha": base},
        "ep0",
    )
    r1 = RunResult(
        0,
        "R1 output\n",
        tmp_path / "R1",
        checkout,
        transcript,
        {"verification": {"exit_reason": "COMPLETED"}},
        "ep1",
        "same",
        "same",
        base,
    )
    return r0, r1, base


def _apply_mutation(checkout: Path, tmp_path: Path, mutation: str, base: str) -> None:
    if mutation == "none":
        pass
    elif mutation == "edit":
        (checkout / "src/a.py").write_text("candidate\n")
    elif mutation == "new":
        (checkout / "src/new.py").write_text("new\n")
    elif mutation == "delete":
        (checkout / "src/a.py").unlink()
    elif mutation == "chmod":
        (checkout / "src/a.py").chmod(0o755)
    elif mutation == "chmod_minus":
        (checkout / "src/a.py").chmod(0o644)
    elif mutation == "commit":
        (checkout / "src/a.py").write_text("committed edit\n")
        _git(checkout, "add", "src/a.py")
        _git(checkout, "-c", "user.name=test", "-c", "user.email=t@invalid", "commit", "-qm", "R1")
    elif mutation == "ignored":
        (checkout / "src/new.log").write_text("ignored\n")
    elif mutation == "eleven":
        for index in range(11):
            (checkout / f"src/{index}.py").write_text("candidate\n")
    elif mutation == "wte":
        (checkout / "src/.gitattributes").write_text("*.py working-tree-encoding=UTF-16\n")
        (checkout / "src/a.py").write_bytes("candidate\n".encode("utf-16"))
    elif mutation == "crlf":
        (checkout / "src/.gitattributes").write_text("*.py text\n")
        (checkout / "src/a.py").write_bytes(b"candidate\r\n")
    elif mutation == "ident":
        (checkout / "src/.gitattributes").write_text("*.py ident\n")
        (checkout / "src/a.py").write_text("value='$Id: changed $'\n")
    elif mutation == "symlink_commit":
        (checkout / "src/link").symlink_to("a.py")
        _git(checkout, "add", "src/link")
        _git(
            checkout, "-c", "user.name=test", "-c", "user.email=t@invalid", "commit", "-qm", "link"
        )
    elif mutation == "conditional_include":
        config = tmp_path / "include.cfg"
        config.write_text('[filter "x"]\n clean = "cat"\n')
        with (checkout / ".git/config").open("a") as stream:
            stream.write(f'\n[includeIf "gitdir:**"]\n path = {config}\n')
        (checkout / "src/.gitattributes").write_text("* filter=x\n")
        (checkout / "src/a.py").write_text("candidate\n")
    elif mutation == "worktree":
        alternate = tmp_path / "alternate"
        alternate.mkdir()
        (alternate / "src").mkdir()
        (alternate / "src/a.py").write_text("alternate\n")
        _git(checkout, "config", "core.worktree", str(alternate))
    elif mutation == "autocrlf":
        _git(checkout, "config", "core.autocrlf", "input")
        (checkout / "src/a.py").write_bytes(b"candidate\r\n")
    elif mutation in {"assume_unchanged", "skip_worktree"}:
        flag = "--assume-unchanged" if mutation == "assume_unchanged" else "--skip-worktree"
        _git(checkout, "update-index", flag, "src/a.py")
        (checkout / "src/a.py").write_text("candidate\n")
    elif mutation == "replace_ref":
        (checkout / "src/a.py").write_text("candidate\n")
        _git(checkout, "add", "src/a.py")
        _git(
            checkout,
            "-c",
            "user.name=test",
            "-c",
            "user.email=t@invalid",
            "commit",
            "-qm",
            "replacement",
        )
        replacement = _git(checkout, "rev-parse", "HEAD")
        _git(checkout, "replace", base, replacement)
    elif mutation == "fifo":
        os.mkfifo(checkout / "outside.pipe")


def _run(tmp_path: Path, mutation: str, judge: CoreJudge):
    r0, r1, _ = _case(tmp_path, mutation)
    return run_flow(
        [],
        task_id="task",
        planner=cast(Any, Planner()),
        gate=cast(Any, Gate()),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, lambda mode, _args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=judge,
    )


def _verify_raises(error: Exception):
    def verify(_intake: Any, **_kwargs: Any) -> dict[str, str]:
        raise error

    return verify


@pytest.mark.parametrize(
    "error",
    [
        TaskRejected("BAD_IMPORT_PATH", "rejected"),
        IntakeRefused("VERIFY_NOT_ACCEPTING", "refused"),
        WorkspaceError("workspace"),
        GitCommandError("failed"),
        KeyError("key"),
        TypeError("type"),
        AttributeError("attribute"),
        Exception("plain"),
    ],
    ids=[
        "task-rejected",
        "intake-refused",
        "workspace-error",
        "git-error",
        "key",
        "type",
        "attribute",
        "plain",
    ],
)
@pytest.mark.parametrize("count", [0, 11], ids=["no-changes", "eleven-files"])
def test_any_core_exception_keeps_r0_and_builds_failure_receipt(tmp_path, error, count):
    mutation = "none" if count == 0 else "eleven"
    r0, r1, _ = _case(tmp_path, mutation)
    judge = CoreJudge(tmp_path / "state", (), verify=_verify_raises(error))
    result, rec = run_flow(
        [],
        task_id="task",
        planner=cast(Any, Planner()),
        gate=cast(Any, Gate()),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, lambda mode, _args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=judge,
    )
    assert result is r0 and result.code == 7
    assert rec["intervened"] is False and rec["reason"] == "PAUSE_R1_NOT_VERIFIED"


def test_cli_keeps_r0_writes_receipt_and_returns_r0_code_after_judge_exception(tmp_path, capsys):
    from experimental.pause import cli

    r0, r1, _ = _case(tmp_path)
    judge = CoreJudge(tmp_path / "state", (), verify=_verify_raises(Exception("judge failed")))
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    out = tmp_path / "out"
    out.mkdir()

    def worker(mode: str, _args: list[str], **_kwargs: Any) -> RunResult:
        return r0 if mode == "run" else r1

    def factory(_config: str) -> tuple[Any, ...]:
        return Planner(), Gate(), SETTINGS, SETTINGS, worker, judge

    code = cli.main(
        [
            "run",
            "--planner-config",
            "ignored",
            "--task",
            str(task),
            "--checkout",
            str(tmp_path / "R0/checkout"),
            "--out",
            str(out),
            "--model",
            "m",
            "--context-cap",
            "1",
            "--canary-report",
            "c",
        ],
        run_factory=factory,
    )
    receipt_path = out / "pause-receipts.jsonl"
    output = capsys.readouterr().out
    assert code == r0.code and receipt_path.is_file()
    assert "R0 output" in output
    assert json.loads(receipt_path.read_text().splitlines()[0])["reason"] == "PAUSE_R1_NOT_VERIFIED"


@pytest.mark.parametrize(
    "mutation",
    [
        "ignored",
        "wte",
        "crlf",
        "ident",
        "symlink_commit",
        "conditional_include",
        "worktree",
        "autocrlf",
        "assume_unchanged",
        "skip_worktree",
        "replace_ref",
        "fifo",
    ],
)
def test_git_normalisation_and_nonregular_entries_keep_r0(tmp_path, mutation):
    r0, r1, _ = _case(tmp_path, mutation)
    judge = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "VERIFIED"})
    result, rec = run_flow(
        [],
        task_id="task",
        planner=cast(Any, Planner()),
        gate=cast(Any, Gate()),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, lambda mode, _args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=judge,
    )
    assert result is r0 and rec["intervened"] is False
    assert rec["reason"] in {"PAUSE_SCOPE_VIOLATION", "PAUSE_R1_NOT_VERIFIED"}


@pytest.mark.parametrize("mutation", ["edit", "new", "delete", "chmod", "chmod_minus", "commit"])
def test_verified_in_scope_regular_changes_choose_r1(tmp_path, mutation):
    r0, r1, _ = _case(tmp_path, mutation)
    judge = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "VERIFIED"})
    result, rec = run_flow(
        [],
        task_id="task",
        planner=cast(Any, Planner()),
        gate=cast(Any, Gate()),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, lambda mode, _args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=judge,
    )
    assert result is r1 and rec["intervened"] is True and rec["core_verdict"] == "VERIFIED"


def test_r1_base_mismatch_is_scope_violation(tmp_path):
    r0, r1, _ = _case(tmp_path)
    r1 = replace(r1, base_sha="f" * 40)
    judge = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "VERIFIED"})
    result, rec = run_flow(
        [],
        task_id="task",
        planner=cast(Any, Planner()),
        gate=cast(Any, Gate()),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, lambda mode, _args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=judge,
    )
    assert result is r0 and rec["reason"] == "PAUSE_SCOPE_VIOLATION"


@pytest.mark.parametrize("mutation", ["conditional_include", "worktree", "autocrlf", "replace_ref"])
def test_r1_git_config_and_replace_ref_tampering_fails_scope(tmp_path, mutation):
    _r0, r1, base = _case(tmp_path, mutation)
    assert not _scope_ok(r1.checkout, ["src"], base)
