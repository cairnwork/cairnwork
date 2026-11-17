"""Race regressions for binding Core's verdict to its source snapshot."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    "race_window",
    ["after-entry-before-snapshot", "between-shape-checks"],
)
def test_judge_rejects_transient_source_change_before_core_snapshot(
    tmp_path: Path, monkeypatch: Any, race_window: str
) -> None:
    """The tree bound to the verdict must be the tree represented by Core's copy."""
    from experimental.pause import core_judge

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("A\n")
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(0, "", tmp_path, repo, transcript, {}, base_sha=base)
    original_shape_check = core_judge._validate_checkout_shape
    shape_calls = 0
    verify_calls: list[bool] = []

    def insert_transient_change() -> None:
        (repo / "a").write_text("B\n")

    if race_window == "after-entry-before-snapshot":
        original_tree_entries = core_judge._tree_entries

        def tree_entries_then_change(path: Path, candidate: str):
            if path == repo:
                insert_transient_change()
            return original_tree_entries(path, candidate)

        monkeypatch.setattr(core_judge, "_tree_entries", tree_entries_then_change)
    else:

        def shape_check_then_change(path: Path, entries: dict, base_paths: set):
            nonlocal shape_calls
            checked = original_shape_check(path, entries, base_paths)
            shape_calls += 1
            if shape_calls == 1:
                insert_transient_change()
            return checked

        monkeypatch.setattr(core_judge, "_validate_checkout_shape", shape_check_then_change)

    def verify(_intake: Any, **_kwargs: Any) -> dict[str, str]:
        verify_calls.append(True)
        (repo / "a").write_text("A\n")
        return {"status": "VERIFIED"}

    judge = CoreJudge(tmp_path / "state", (), verify=verify)
    with pytest.raises(RuntimeError, match="Core judge changed R1 checkout"):
        judge(result)
    assert verify_calls == [True]
    assert (repo / "a").read_text() == "A\n"


def test_judge_rejects_excluded_directory_file_added_during_clone(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A file appearing in an excluded empty directory must be represented in Core's copy."""
    from experimental.pause import core_judge

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "empty-after-delete").mkdir()
    (repo / "empty-after-delete" / "tracked").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "empty-after-delete" / "tracked").unlink()
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(0, "", tmp_path, repo, transcript, {}, base_sha=base)
    original_clone = core_judge._clone
    verify_calls: list[bool] = []

    def clone_then_add_excluded_file(source: Path, destination: Path) -> None:
        original_clone(source, destination)
        (repo / "empty-after-delete" / "during-clone").write_text("transient\n")

    def verify(_intake: Any, **_kwargs: Any) -> dict[str, str]:
        verify_calls.append(True)
        (repo / "empty-after-delete" / "during-clone").unlink(missing_ok=True)
        return {"status": "VERIFIED"}

    monkeypatch.setattr(core_judge, "_clone", clone_then_add_excluded_file)
    with pytest.raises(ValueError, match="shape changed"):
        CoreJudge(tmp_path / "state", (), verify=verify)(result)
    assert verify_calls == []
    (repo / "empty-after-delete" / "during-clone").unlink(missing_ok=True)
