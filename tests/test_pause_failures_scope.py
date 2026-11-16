"""AC4 fallback coverage for out-of-scope and symlink writes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from pause_fakes import FakeGate, FakePlanner

from experimental.pause import receipt, schema
from experimental.pause.flow import RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply

SETTINGS = CallSettings("configured-model", "low", 1, 1)
OUT_OF_SCOPE_ROWS = (
    "edit",
    "delete",
    "new",
    "ignored",
    "nested",
    "lookalike-a",
    "lookalike-b",
    "chmod-executable",
    "rename-to-outside",
    "root-dotfile",
)
SYMLINK_ROWS = (
    "symlink",
    "symlink-file",
    "symlink-dir",
    "outside-target",
    "absolute-target",
    "replace-in-scope-with-outside-link",
    "dangling-link",
    "symlink-chain",
    "directory-link",
    "git-config-link",
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _fixture(root: Path, *, tracked_outside: bool = False) -> tuple[list[str], Path, RunResult]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Pause test")
    _git(repo, "config", "user.email", "pause@example.invalid")
    (repo / "app.py").write_text("base\n")
    if tracked_outside:
        (repo / "outside.py").write_text("tracked\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    transcript = root / "r0-transcript.json"
    parent = {
        "messages": [{"role": "user", "content": "task"}, {"role": "assistant"}],
        "turns": [{"index": 0, "assistant_msg_index": 1}],
        "write_scope": ["src"],
    }
    transcript.write_text(json.dumps(parent))
    args = ["--checkout", str(repo), "--out", str(root / "out")]
    record = {"base_sha": base, "first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 0}}
    r0 = RunResult(0, "r0", root, repo, transcript, record, "ep_r0")
    return args, repo, r0


def _assert_constructed(checkout: Path, mutation: str) -> None:
    if mutation in {
        "symlink",
        "outside-target",
        "absolute-target",
        "symlink-chain",
        "git-config-link",
    }:
        assert (checkout / "outside.py").is_symlink()
    elif mutation in {"symlink-file", "replace-in-scope-with-outside-link"}:
        assert (checkout / "app.py").is_symlink()
    elif mutation in {"symlink-dir", "directory-link"}:
        assert (checkout / "outside.py").is_symlink()
        assert (checkout / "outside.py").is_dir()
    elif mutation == "dangling-link":
        assert (checkout / "outside.py").is_symlink()
        assert not (checkout / "outside.py").exists()
    elif mutation == "edit":
        assert (checkout / "app.py").read_text() == "edited outside scope\n"
    elif mutation == "delete":
        assert not (checkout / "app.py").exists()
    elif mutation == "nested":
        assert (checkout / "deep" / "x.py").exists()
    elif mutation.startswith("lookalike"):
        assert (checkout / ("src.." if mutation.endswith("a") else "src2")).exists()
    elif mutation == "ignored":
        assert (checkout / ".gitignore").exists() and (checkout / "outside.py").exists()
    elif mutation == "chmod-executable":
        assert (checkout / "outside.py").stat().st_mode & 0o111
    elif mutation == "rename-to-outside":
        assert (checkout / "moved.py").exists() and not (checkout / "app.py").exists()
    else:
        assert (checkout / "outside.py").exists() or (checkout / ".secret").exists()


def _apply_mutation(checkout: Path, tmp_path: Path, mutation: str, symlink: bool) -> None:
    if symlink:
        outside = tmp_path / "external-target"
        outside.write_text("external\n")
        if mutation == "symlink":
            (checkout / "outside.py").symlink_to("app.py")
        elif mutation == "outside-target":
            (checkout / "outside.py").symlink_to(os.path.relpath(outside, checkout))
        elif mutation == "absolute-target":
            (checkout / "outside.py").symlink_to(outside)
        elif mutation in {"symlink-file", "replace-in-scope-with-outside-link"}:
            (checkout / "app.py").unlink()
            (checkout / "app.py").symlink_to(outside)
        elif mutation in {"symlink-dir", "directory-link"}:
            directory = tmp_path / "outside-target-dir"
            directory.mkdir()
            (checkout / "outside.py").symlink_to(directory, target_is_directory=True)
        elif mutation == "dangling-link":
            (checkout / "outside.py").symlink_to(tmp_path / "missing-target")
        elif mutation == "symlink-chain":
            (checkout / "outside.py").symlink_to("link-two")
            (checkout / "link-two").symlink_to("app.py")
        elif mutation == "git-config-link":
            (checkout / "outside.py").symlink_to(checkout / ".git" / "config")
        else:
            raise AssertionError(mutation)
    elif mutation == "delete":
        (checkout / "app.py").unlink()
    elif mutation == "edit":
        (checkout / "app.py").write_text("edited outside scope\n")
    elif mutation == "nested":
        (checkout / "deep").mkdir()
        (checkout / "deep" / "x.py").write_text("x")
    elif mutation.startswith("lookalike"):
        (checkout / ("src.." if mutation.endswith("a") else "src2")).write_text("x")
    elif mutation == "ignored":
        (checkout / ".gitignore").write_text("outside.py\n")
        (checkout / "outside.py").write_text("outside\n")
    elif mutation == "chmod-executable":
        (checkout / "outside.py").chmod(0o755)
    elif mutation == "rename-to-outside":
        (checkout / "app.py").rename(checkout / "moved.py")
    elif mutation == "root-dotfile":
        (checkout / ".secret").write_text("outside\n")
    else:
        (checkout / "outside.py").write_text("outside\n")
    _assert_constructed(checkout, mutation)


def _resume_flow(tmp_path: Path, mutation: str, *, symlink: bool) -> None:
    args, repo, r0 = _fixture(tmp_path, tracked_outside=mutation == "chmod-executable")

    def resume(mode: str, _args: list[str], **_kwargs: Any) -> RunResult:
        if mode == "run":
            return r0
        root = tmp_path / "runs" / mutation
        root.mkdir(parents=True)
        checkout = root / "checkout"
        shutil.copytree(repo, checkout, symlinks=True)
        _apply_mutation(checkout, tmp_path, mutation, symlink)
        return RunResult(
            0,
            "r1",
            root,
            checkout,
            guard_before_plan="a" * 64,
            guard_at_plan_arrival="a" * 64,
            record={"verification": {"exit_reason": "COMPLETED"}},
        )

    result, rec = run_flow(
        args,
        task_id="scope",
        planner=FakePlanner([PlanReply("edit", "configured-model", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "configured-model", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, resume),
    )
    assert result is r0 and rec["reason"] == schema.Reason.PAUSE_SCOPE_VIOLATION
    assert receipt.verify(rec) == []
    assert rec["intervened"] is False


@pytest.mark.parametrize("mutation", OUT_OF_SCOPE_ROWS, ids=OUT_OF_SCOPE_ROWS)
def test_ac4_r1_out_of_scope_writes_fall_back(tmp_path: Path, mutation: str) -> None:
    _resume_flow(tmp_path, mutation, symlink=False)


@pytest.mark.parametrize("mutation", SYMLINK_ROWS, ids=SYMLINK_ROWS)
def test_ac4_r1_symlink_writes_are_refused(tmp_path: Path, mutation: str) -> None:
    _resume_flow(tmp_path, mutation, symlink=True)
