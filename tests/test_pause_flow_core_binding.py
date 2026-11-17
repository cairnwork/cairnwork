"""Flow-level regressions for the Core source-tree binding."""

import json
import os
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


def _excluded_flow_checkout(
    tmp_path: Path, r0: RunResult
) -> tuple[RunResult, RunResult, Path, str]:
    r0, checkout, _base = _checkout(tmp_path, r0)
    (checkout / "D").mkdir()
    (checkout / "D" / "tracked").write_text("base\n")
    _git(checkout, "add", "D/tracked")
    _git(checkout, "commit", "-qm", "excluded directory base")
    base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    (checkout / "D" / "tracked").unlink()
    r0 = replace(r0, record={**(r0.record or {}), "base_sha": base})
    r1 = replace(
        r0,
        root=tmp_path / "R1",
        checkout=checkout,
        base_sha=base,
        guard_before_plan="digest",
        guard_at_plan_arrival="digest",
        git_config_sha256="0" * 64,
        record={
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
        },
    )
    return r0, r1, checkout, base


@pytest.mark.parametrize(
    "kind", ["file", "executable", "symlink", "nested-file", "dotfile", "empty-dir"]
)
def test_flow_keeps_r0_when_excluded_entry_crosses_judge_snapshot(
    tmp_path: Path, monkeypatch: Any, kind: str
) -> None:
    """The flow snapshot must match the tree Core actually receives."""
    import os
    import shutil

    from experimental.pause import core_judge
    from experimental.pause.guard import TreeGuard

    r0, r1, checkout, _base = _excluded_flow_checkout(tmp_path, _scenario(tmp_path)[0])
    directory = checkout / "D"

    def add() -> None:
        if kind == "file":
            (directory / "x").write_text("transient\n")
        elif kind == "executable":
            path = directory / "x"
            path.write_text("transient\n")
            path.chmod(0o755)
        elif kind == "symlink":
            os.symlink("../a", directory / "x")
        elif kind == "nested-file":
            (directory / "E").mkdir()
            (directory / "E" / "x").write_text("transient\n")
        elif kind == "dotfile":
            (directory / ".x").write_text("transient\n")
        else:
            (directory / "E").mkdir()

    def remove() -> None:
        for entry in directory.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    monkeypatch.setattr("experimental.pause.flow._scope_ok", lambda *_args: True)
    original_shape = core_judge._validate_checkout_shape
    original_pair = TreeGuard.digest_pair
    shape_calls = 0

    def shape(path: Path, entries: dict, paths: set):
        nonlocal shape_calls
        excluded = original_shape(path, entries, paths)
        shape_calls += 1
        if shape_calls == 1:
            add()
        return excluded

    def pair(guard, **kwargs):
        digests = original_pair(guard, **kwargs)
        remove()
        return digests

    monkeypatch.setattr(core_judge, "_validate_checkout_shape", shape)
    monkeypatch.setattr(TreeGuard, "digest_pair", pair)
    add()

    def judge_with_flow_window(item):
        remove()
        verdict = CoreJudge(
            tmp_path / "state",
            (),
            verify=lambda *_a, **_kw: add() or {"status": "VERIFIED"},
        )(item)
        return verdict

    result, receipt = run_flow(
        [],
        task_id="task",
        planner=Planner(),
        gate=Gate(),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=lambda mode, _args, **_kwargs: r0 if mode == "run" else r1,
        core_verdict=judge_with_flow_window,
    )
    assert result is r0
    assert receipt["reason"] == "PAUSE_R1_NOT_VERIFIED"


@pytest.mark.parametrize("status", ["VERIFIED", "FAILED"])
def test_flow_records_stable_core_tree_and_selects_only_verified(
    tmp_path: Path, monkeypatch: Any, status: str
) -> None:
    """Stable excluded empty directories preserve Core's verdict and bound digest."""
    from experimental.pause.guard import TreeGuard

    r0, r1, checkout, base = _excluded_flow_checkout(tmp_path, _scenario(tmp_path)[0])
    (checkout / "D" / "E").mkdir()
    (checkout / "D" / "E" / "tracked").write_text("nested\n")
    _git(checkout, "add", "D/E/tracked")
    _git(checkout, "commit", "-qm", "nested directory base")
    base = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    (checkout / "D" / "E" / "tracked").unlink()
    r0 = replace(r0, record={**(r0.record or {}), "base_sha": base})
    r1 = replace(r1, base_sha=base)
    entry = TreeGuard(checkout).digest()
    monkeypatch.setattr("experimental.pause.flow._scope_ok", lambda *_args: True)
    result, receipt = run_flow(
        [],
        task_id="task",
        planner=Planner(),
        gate=Gate(),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=lambda mode, _args, **_kwargs: r0 if mode == "run" else r1,
        core_verdict=CoreJudge(
            tmp_path / "state", (), verify=lambda *_a, **_kw: {"status": status}
        ),
    )
    assert receipt["core_tree_sha256"] == entry
    assert receipt["core_verdict"] == status
    assert result is (r1 if status == "VERIFIED" else r0)


def _swap_excluded_path(repo: Path, outside: Path, kind: str) -> None:
    directory = repo / "D"
    directory.rmdir()
    if kind in {"file-0644", "file-0755"}:
        directory.write_text("transient\n")
        directory.chmod(0o644 if kind == "file-0644" else 0o755)
    elif kind == "symlink-a":
        os.symlink("a", directory)
    else:
        os.symlink(str(outside), directory)


def _restore_excluded_path(repo: Path) -> None:
    directory = repo / "D"
    directory.unlink()
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)


@pytest.mark.parametrize("kind", ["file-0644", "file-0755", "symlink-a", "symlink-outside"])
@pytest.mark.parametrize(
    "point", ["after-digest-pair", "before-clone", "after-clone", "before-copy"]
)
def test_flow_core_tree_matches_snapshot_variants(
    tmp_path: Path, monkeypatch: Any, kind: str, point: str
) -> None:
    from experimental.pause import core_judge

    r0, _r1, checkout, base = _excluded_flow_checkout(tmp_path, _scenario(tmp_path)[0])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("outside\n")
    pair = TreeGuard.digest_pair
    clone = core_judge._clone
    copy = core_judge._copy_working_tree

    def swap() -> None:
        _swap_excluded_path(checkout, outside, kind)

    if point == "after-digest-pair":

        def pair_then_swap(guard, **kwargs):
            value = pair(guard, **kwargs)
            swap()
            return value

        monkeypatch.setattr(TreeGuard, "digest_pair", pair_then_swap)
    elif point == "before-clone":

        def clone_after_swap(source, destination):
            swap()
            return clone(source, destination)

        monkeypatch.setattr(core_judge, "_clone", clone_after_swap)
    elif point == "after-clone":

        def clone_then_swap(source, destination):
            clone(source, destination)
            swap()

        monkeypatch.setattr(core_judge, "_clone", clone_then_swap)
    else:

        def copy_after_swap(source, destination):
            swap()
            return copy(source, destination)

        monkeypatch.setattr(core_judge, "_copy_working_tree", copy_after_swap)

    copy_with_injection = core_judge._copy_working_tree

    def copy_then_restore(source, destination):
        value = copy_with_injection(source, destination)
        _restore_excluded_path(checkout)
        return value

    monkeypatch.setattr(core_judge, "_copy_working_tree", copy_then_restore)
    monkeypatch.setattr("experimental.pause.flow._scope_ok", lambda *_args: True)
    r1 = replace(
        r0,
        root=tmp_path / "R1",
        checkout=checkout,
        base_sha=base,
        guard_before_plan="digest",
        guard_at_plan_arrival="digest",
        git_config_sha256="0" * 64,
        record={
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
        },
    )
    result, receipt = run_flow(
        [],
        task_id="task",
        planner=Planner(),
        gate=Gate(),
        planner_settings=SETTINGS,
        gate_settings=SETTINGS,
        runner=lambda mode, _args, **_kwargs: r0 if mode == "run" else r1,
        core_verdict=CoreJudge(
            tmp_path / "state", (), verify=lambda *_a, **_kw: {"status": "VERIFIED"}
        ),
    )
    assert result is r0, receipt
    assert receipt["reason"] == "PAUSE_R1_NOT_VERIFIED"
