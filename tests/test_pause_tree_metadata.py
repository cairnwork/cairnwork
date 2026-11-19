"""PAUSE review regressions for filesystem state Git does not carry."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from experimental.pause.core_judge import CoreJudge, _entries
from experimental.pause.flow import CoreVerdict, RunResult, run_flow
from experimental.pause.interfaces import CallSettings, GateReply, PlanReply
from experimental.pause.scope import _scope_ok

_SETTINGS = CallSettings("m", "low", 1.0, 1)


class _Planner:
    def plan(self, _request: Any) -> PlanReply:
        return PlanReply("plan", "p", "low")


class _Gate:
    def review(self, _request: Any) -> GateReply:
        return GateReply("ACCEPT", "g", "low")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(root: Path, *, adjacent_symlinks: bool = False) -> tuple[Path, str]:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "src/a.py").write_text("base\n")
    (root / "src/keep.py").write_text("keep\n")
    (root / "outside.txt").write_text("outside\n")
    if adjacent_symlinks:
        (root / "link-a").symlink_to("src")
        (root / "link-b").symlink_to("src")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _results(tmp_path: Path, mutation: str) -> tuple[RunResult, RunResult, str]:
    checkout, base = _repo(
        tmp_path / "R1" / "checkout", adjacent_symlinks=mutation == "adjacent_symlinks"
    )
    file = checkout / "src/a.py"
    if mutation == "edit":
        file.write_text("candidate\n")
    elif mutation == "new_nested":
        (checkout / "src/nested").mkdir()
        (checkout / "src/nested/new.py").write_text("new\n")
    elif mutation == "delete":
        file.unlink()
    elif mutation == "chmod_plus":
        file.chmod(0o755)
    elif mutation == "chmod_minus":
        file.chmod(0o755)
        file.chmod(0o644)
    elif mutation == "empty_inside":
        (checkout / "src/empty").mkdir()
    elif mutation == "empty_outside":
        (checkout / "empty").mkdir()
    elif mutation == "mode_0444":
        file.write_text("candidate\n")
        file.chmod(0o444)
    elif mutation == "mode_0600":
        file.write_text("candidate\n")
        file.chmod(0o600)
    elif mutation == "dir_0555":
        file.write_text("candidate\n")
        (checkout / "src").chmod(0o555)
    elif mutation == "xattr":
        file.write_text("candidate\n")
        _set_xattr(file)
    elif mutation == "acl":
        file.write_text("candidate\n")
        _set_acl(file)
    elif mutation == "adjacent_symlinks":
        file.write_text("candidate\n")
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
        "R0\n",
        tmp_path / "R0",
        tmp_path / "R0/checkout",
        transcript,
        {"first_edit_cut": {"cut_status": "CUT", "cut_turn_index": 2}, "base_sha": base},
        "ep0",
    )
    r1 = RunResult(
        0,
        "R1\n",
        tmp_path / "R1",
        checkout,
        transcript,
        {"verification": {"exit_reason": "COMPLETED"}},
        "ep1",
        "same",
        "same",
        base,
        hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest(),
    )
    return r0, r1, base


def _set_xattr(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-w", "user.cw", "planted", str(path)], check=True)
        return
    try:
        os.setxattr(path, "user.cw", b"planted")
    except OSError as exc:
        pytest.skip(f"extended attributes unavailable: {exc}")


def _set_acl(path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS extended ACLs are unavailable on this platform")
    subprocess.run(["chmod", "+a", "everyone deny write", str(path)], check=True)


def _flow(tmp_path: Path, mutation: str, error: BaseException | None = None):
    r0, r1, _ = _results(tmp_path, mutation)
    core = CoreJudge(
        tmp_path / "state", (), verify=lambda *_args, **_kwargs: {"status": "VERIFIED"}
    )

    def judge(_r1: RunResult) -> CoreVerdict:
        if error is not None:
            raise error
        return core(_r1)

    result, receipt = run_flow(
        [],
        task_id="task",
        planner=cast(Any, _Planner()),
        gate=cast(Any, _Gate()),
        planner_settings=_SETTINGS,
        gate_settings=_SETTINGS,
        runner=cast(Any, lambda mode, _args, **_kwargs: r0 if mode == "run" else r1),
        core_verdict=cast(Any, judge),
    )
    return result, receipt


@pytest.mark.parametrize(
    "mutation",
    ["empty_inside", "empty_outside", "mode_0444", "mode_0600", "dir_0555", "xattr", "acl"],
    ids=[
        "empty-dir-in-scope",
        "empty-dir-out-of-scope",
        "file-mode-0444",
        "file-mode-0600",
        "directory-mode-0555",
        "changed-file-xattr",
        "changed-file-acl",
    ],
)
def test_unrepresented_tree_state_keeps_r0(tmp_path: Path, mutation: str) -> None:
    result, rec = _flow(tmp_path, mutation)
    assert result.code == 7 and rec["intervened"] is False
    assert rec["reason"] == "PAUSE_R1_NOT_VERIFIED"


def test_judge_systemexit_keeps_r0(tmp_path: Path) -> None:
    result, rec = _flow(tmp_path, "edit", SystemExit(3))
    assert result.code == 7 and rec["intervened"] is False
    assert rec["reason"] == "PAUSE_R1_NOT_VERIFIED"


def test_judge_keyboardinterrupt_propagates(tmp_path: Path) -> None:
    # #1417: Ctrl-C must propagate, not be turned into PAUSE_R1_NOT_VERIFIED.
    with pytest.raises(KeyboardInterrupt):
        _flow(tmp_path, "edit", KeyboardInterrupt())


@pytest.mark.parametrize(
    "mutation",
    ["edit", "new_nested", "delete", "chmod_plus", "chmod_minus", "adjacent_symlinks"],
    ids=[
        "tracked-edit",
        "new-file-nonempty-subdir",
        "delete-file",
        "chmod-plus-x",
        "chmod-minus-x",
        "adjacent-dir-symlinks",
    ],
)
def test_git_representable_change_selects_r1(tmp_path: Path, mutation: str) -> None:
    result, rec = _flow(tmp_path, mutation)
    assert result.code == 0 and rec["intervened"] is True
    assert rec["core_verdict"] == "VERIFIED"


def test_scope_rejects_nonregular_untracked_entry(tmp_path: Path) -> None:
    checkout, base = _repo(tmp_path / "checkout")
    os.mkfifo(checkout / "outside.pipe")
    pin = hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest()
    assert not _scope_ok(checkout, ["src"], base, pin)


def test_judge_rejects_nonregular_checkout_entry(tmp_path: Path) -> None:
    checkout, _base = _repo(tmp_path / "checkout")
    os.mkfifo(checkout / "outside.pipe")
    with pytest.raises(ValueError, match="non-regular"):
        _entries(checkout)


def test_scope_pins_top_level_check(tmp_path: Path, monkeypatch: Any) -> None:
    checkout, base = _repo(tmp_path / "checkout")
    monkeypatch.setattr("experimental.pause.scope._git_output", lambda *_a: "/elsewhere")
    pin = hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest()
    assert not _scope_ok(checkout, ["src"], base, pin)


def test_scope_pins_core_worktree_refusal(tmp_path: Path) -> None:
    checkout, base = _repo(tmp_path / "checkout")
    _git(checkout, "config", "core.worktree", str(tmp_path / "elsewhere"))
    from experimental.pause.scope import _repo_metadata_ok

    pin = hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest()
    assert _repo_metadata_ok(checkout, pin) is False


def test_scope_pins_core_filemode_false_refusal(tmp_path: Path) -> None:
    checkout, base = _repo(tmp_path / "checkout")
    _git(checkout, "config", "core.filemode", "false")
    pin = hashlib.sha256((checkout / ".git" / "config").read_bytes()).hexdigest()
    assert not _scope_ok(checkout, ["src"], base, pin)
