"""AC4 fallback coverage for out-of-scope and symlink writes."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from pause_fakes import FakeGate, FakePlanner

from experimental.pause import receipt, schema
from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.guard import TreeGuard
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
    "new-link-repo-file",
    "new-link-absolute-outside",
    "new-directory-link-outside",
    "tracked-file-replaced-link",
    "new-dangling-link",
    "new-link-relative-parent",
    "new-link-git-config",
    "new-link-chain",
    "new-link-src-file",
    "new-directory-link-src-dir",
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
    (repo / "src").mkdir()
    (repo / "src" / "ok.py").write_text("base\n")
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
    src = checkout / "src"
    if mutation == "tracked-file-replaced-link":
        assert (src / "ok.py").is_symlink()
    elif mutation == "new-dangling-link":
        assert (src / "dangling.py").is_symlink() and not (src / "dangling.py").exists()
    elif mutation == "new-directory-link-outside":
        assert (src / "outside-dir").is_symlink() and (src / "outside-dir").is_dir()
    elif mutation == "new-directory-link-src-dir":
        assert (src / "inside-dir").is_symlink() and (src / "inside-dir").is_dir()
    elif mutation == "new-link-chain":
        assert (src / "chain-a.py").is_symlink() and (src / "chain-b.py").is_symlink()
    elif mutation.startswith("new-link-"):
        filename = {
            "new-link-repo-file": "repo-file.py",
            "new-link-absolute-outside": "absolute.py",
            "new-link-relative-parent": "parent.py",
            "new-link-git-config": "git-config.py",
            "new-link-src-file": "inside.py",
        }[mutation]
        assert (src / filename).is_symlink()
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
        assert (checkout / ".secret").exists()


def _apply_symlink(checkout: Path, tmp_path: Path, mutation: str) -> None:
    src = checkout / "src"
    external = tmp_path / "external-target"
    external.write_text("external\n")
    if mutation == "new-link-repo-file":
        (src / "repo-file.py").symlink_to("../outside.py")
    elif mutation == "new-link-absolute-outside":
        (src / "absolute.py").symlink_to(external)
    elif mutation == "new-directory-link-outside":
        directory = tmp_path / "outside-target-dir"
        directory.mkdir()
        (src / "outside-dir").symlink_to(directory, target_is_directory=True)
    elif mutation == "tracked-file-replaced-link":
        (src / "ok.py").unlink()
        (src / "ok.py").symlink_to(external)
    elif mutation == "new-dangling-link":
        (src / "dangling.py").symlink_to(tmp_path / "missing-target")
    elif mutation == "new-link-relative-parent":
        (src / "parent.py").symlink_to("../../external-target")
    elif mutation == "new-link-git-config":
        (src / "git-config.py").symlink_to(checkout / ".git" / "config")
    elif mutation == "new-link-chain":
        (src / "chain-a.py").symlink_to("chain-b.py")
        (src / "chain-b.py").symlink_to("ok.py")
    elif mutation == "new-link-src-file":
        (src / "inside.py").symlink_to("ok.py")
    elif mutation == "new-directory-link-src-dir":
        (src / "real-dir").mkdir()
        (src / "inside-dir").symlink_to("real-dir", target_is_directory=True)
    else:
        raise AssertionError(mutation)


def _apply_mutation(checkout: Path, tmp_path: Path, mutation: str) -> None:
    if mutation in SYMLINK_ROWS:
        _apply_symlink(checkout, tmp_path, mutation)
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
    else:
        (checkout / ".secret").write_text("outside\n")
    _assert_constructed(checkout, mutation)


def _resume_flow(
    tmp_path: Path,
    mutation: str,
    *,
    symlink: bool = False,
    core_calls: list[RunResult] | None = None,
) -> tuple[RunResult, dict, RunResult]:
    args, repo, r0 = _fixture(tmp_path, tracked_outside=mutation == "chmod-executable")

    def resume(mode: str, _args: list[str], **_kwargs: Any) -> RunResult:
        if mode == "run":
            return r0
        root = tmp_path / "runs" / mutation
        root.mkdir(parents=True)
        checkout = root / "checkout"
        shutil.copytree(repo, checkout, symlinks=True)
        config_sha = hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest()
        base_sha = cast(Any, r0.record)["base_sha"]
        if symlink:
            _apply_symlink(checkout, tmp_path, mutation)
        else:
            _apply_mutation(checkout, tmp_path, mutation)
        _assert_constructed(checkout, mutation)
        return RunResult(
            0,
            "r1",
            root,
            checkout,
            guard_before_plan="a" * 64,
            guard_at_plan_arrival="a" * 64,
            record={"verification": {"exit_reason": "COMPLETED"}},
            base_sha=base_sha,
            git_config_sha256=config_sha,
        )

    def core_verdict(r1: RunResult) -> CoreVerdict:
        if core_calls is not None:
            core_calls.append(r1)
        return CoreVerdict("VERIFIED", TreeGuard(r1.checkout).digest())

    result, rec = run_flow(
        args,
        task_id="scope",
        planner=FakePlanner([PlanReply("edit", "configured-model", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "configured-model", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, resume),
        core_verdict=core_verdict if core_calls is not None else None,
    )
    return result, rec, r0


@pytest.mark.parametrize("mutation", OUT_OF_SCOPE_ROWS, ids=OUT_OF_SCOPE_ROWS)
def test_ac4_r1_out_of_scope_writes_fall_back(tmp_path: Path, mutation: str) -> None:
    result, rec, r0 = _resume_flow(tmp_path, mutation)
    assert result is r0 and rec["reason"] == schema.Reason.PAUSE_SCOPE_VIOLATION
    assert receipt.verify(rec) == []
    assert rec["intervened"] is False


@pytest.mark.parametrize("mutation", SYMLINK_ROWS, ids=SYMLINK_ROWS)
def test_ac4_r1_symlink_writes_are_refused(tmp_path: Path, mutation: str) -> None:
    result, rec, r0 = _resume_flow(tmp_path, mutation, symlink=True)
    assert result is r0 and rec["reason"] == schema.Reason.PAUSE_SCOPE_VIOLATION
    assert receipt.verify(rec) == []
    assert rec["intervened"] is False


def test_ac4_r1_in_scope_write_is_not_a_scope_violation(tmp_path: Path) -> None:
    args, repo, r0 = _fixture(tmp_path)
    core_calls: list[RunResult] = []

    def resume(mode: str, _args: list[str], **_kwargs: Any) -> RunResult:
        if mode == "run":
            return r0
        root = tmp_path / "runs" / "in-scope"
        root.mkdir(parents=True)
        checkout = root / "checkout"
        shutil.copytree(repo, checkout, symlinks=True)
        (checkout / "src" / "ok.py").write_text("updated in scope\n")
        config_sha = hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest()
        return RunResult(
            0,
            "r1",
            root,
            checkout,
            guard_before_plan="a" * 64,
            guard_at_plan_arrival="a" * 64,
            record={"verification": {"exit_reason": "COMPLETED"}},
            base_sha=cast(Any, r0.record)["base_sha"],
            git_config_sha256=config_sha,
        )

    def core_verdict(r1: RunResult) -> CoreVerdict:
        core_calls.append(r1)
        return CoreVerdict("VERIFIED", TreeGuard(r1.checkout).digest())

    result, rec = run_flow(
        args,
        task_id="scope-in-scope",
        planner=FakePlanner([PlanReply("edit", "configured-model", "low")]),
        gate=FakeGate([GateReply("ACCEPT", "configured-model", "low")]),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=cast(Any, resume),
        core_verdict=core_verdict,
    )
    assert rec["reason"] != schema.Reason.PAUSE_SCOPE_VIOLATION
    assert core_calls == [result] and len(core_calls) == 1, rec
