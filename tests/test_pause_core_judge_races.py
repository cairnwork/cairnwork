"""Race regressions for binding Core's verdict to its source snapshot."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import GuardUnavailable


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


def _excluded_checkout(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "excluded-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("base\n")
    (repo / "D").mkdir()
    (repo / "D" / "tracked").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("candidate\n")
    (repo / "D" / "tracked").unlink()
    return repo, base


def _add_excluded_entry(repo: Path, kind: str) -> None:
    import os

    directory = repo / "D"
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
    elif kind == "fifo":
        os.mkfifo(directory / "x")
    elif kind == "hardlink":
        os.link(repo / "a", directory / "x")
    else:
        (directory / "E").mkdir()


def _remove_excluded_entries(repo: Path) -> None:
    import shutil

    for entry in (repo / "D").iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


@pytest.mark.parametrize(
    "kind",
    ["file", "executable", "symlink", "nested-file", "dotfile", "empty-dir", "fifo", "hardlink"],
)
@pytest.mark.parametrize("remove_at", ["after-pair", "before-clone", "after-clone", "before-copy"])
def test_judge_rejects_entries_under_excluded_directories(
    tmp_path: Path, monkeypatch: Any, kind: str, remove_at: str
) -> None:
    """Excluded directories may contain only excluded directories."""
    from experimental.pause import core_judge
    from experimental.pause.guard import TreeGuard

    repo, base = _excluded_checkout(tmp_path)
    result = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    original_shape = core_judge._validate_checkout_shape
    original_pair = TreeGuard.digest_pair
    original_clone = core_judge._clone
    original_copy = core_judge._copy_working_tree
    shape_calls = 0

    def shape(path: Path, entries: dict, paths: set):
        nonlocal shape_calls
        excluded = original_shape(path, entries, paths)
        shape_calls += 1
        if shape_calls == 1:
            _add_excluded_entry(repo, kind)
        return excluded

    def pair(guard, **kwargs):
        digests = original_pair(guard, **kwargs)
        if remove_at == "after-pair":
            _remove_excluded_entries(repo)
        return digests

    def clone(source: Path, destination: Path) -> None:
        if remove_at == "before-clone":
            _remove_excluded_entries(repo)
        original_clone(source, destination)
        if remove_at == "after-clone":
            _remove_excluded_entries(repo)

    def copy(source: Path, destination: Path):
        if remove_at == "before-copy":
            _remove_excluded_entries(repo)
        return original_copy(source, destination)

    monkeypatch.setattr(core_judge, "_validate_checkout_shape", shape)
    monkeypatch.setattr(TreeGuard, "digest_pair", pair)
    monkeypatch.setattr(core_judge, "_clone", clone)
    monkeypatch.setattr(core_judge, "_copy_working_tree", copy)

    def verify(_intake: Any, **_kwargs: Any) -> dict[str, str]:
        _add_excluded_entry(repo, kind)
        return {"status": "VERIFIED"}

    with pytest.raises(ValueError):
        CoreJudge(tmp_path / "state", (), verify=verify)(result)


@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "excluded-empty",
        "nested-excluded-empty",
        "prefix-siblings-unicode",
        "failed",
        "mixed",
    ],
)
def test_judge_accepts_stable_excluded_shapes_and_records_core_result(
    tmp_path: Path, case: str
) -> None:
    """Stable snapshots retain the digest and Core verdict semantics."""
    repo, base = _excluded_checkout(tmp_path)
    if case in {"excluded-empty", "nested-excluded-empty"}:
        (repo / "D" / "tracked").write_text("base\n")
        if case == "nested-excluded-empty":
            (repo / "D" / "E").mkdir()
            (repo / "D" / "E" / "tracked").write_text("nested\n")
            git(repo, "add", "D/E/tracked")
            git(repo, "commit", "-qm", "nested base")
            base = git(repo, "rev-parse", "HEAD")
            (repo / "D" / "E" / "tracked").unlink()
        (repo / "D" / "tracked").unlink()
    elif case == "prefix-siblings-unicode":
        for name in ("D2/f", "D.x", "src/Dé x/tracked", "src/keep"):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("base\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "prefix and unicode paths")
        base = git(repo, "rev-parse", "HEAD")
        (repo / "D").rmdir()
        (repo / "src/Dé x/tracked").unlink()
        (repo / "D2/f").write_text("changed\n")
    elif case == "mixed":
        (repo / "a").unlink()
        executable = repo / "new-executable"
        executable.write_text("new\n")
        executable.chmod(0o755)
    entry = TreeGuard(repo).digest()
    status = "FAILED" if case == "failed" else "VERIFIED"
    verdict = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_kw: {"status": status})(
        RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    )
    assert (verdict.status, verdict.tree_sha256) == (status, entry)
    assert TreeGuard(repo).digest() == entry


def _swap_excluded_path(repo: Path, outside: Path, kind: str) -> None:
    directory = repo / "D"
    directory.rmdir()
    if kind in {"file-0644", "file-0755"}:
        directory.write_text("transient\n")
        directory.chmod(0o644 if kind == "file-0644" else 0o755)
    elif kind == "symlink-a":
        os.symlink("a", directory)
    elif kind == "symlink-outside":
        os.symlink(str(outside), directory)
    elif kind == "fifo":
        os.mkfifo(directory)
    else:
        os.link(repo / "a", directory)


def _restore_excluded_path(repo: Path) -> None:
    directory = repo / "D"
    directory.unlink()
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)


def _install_excluded_swap_hooks(
    monkeypatch: Any, repo: Path, kind: str, point: str, outside: Path
) -> None:
    from experimental.pause import core_judge

    pair = TreeGuard.digest_pair
    clone = core_judge._clone
    copy = core_judge._copy_working_tree

    def swap() -> None:
        _swap_excluded_path(repo, outside, kind)

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
        _restore_excluded_path(repo)
        return value

    monkeypatch.setattr(core_judge, "_copy_working_tree", copy_then_restore)


@pytest.mark.parametrize(
    "kind",
    ["file-0644", "file-0755", "symlink-a", "symlink-outside"],
)
@pytest.mark.parametrize(
    "point", ["after-digest-pair", "before-clone", "after-clone", "before-copy"]
)
def test_judge_core_tree_matches_snapshot_variants(
    tmp_path: Path, monkeypatch: Any, kind: str, point: str
) -> None:
    repo, base = _excluded_checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("outside\n")
    result = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    _install_excluded_swap_hooks(monkeypatch, repo, kind, point, outside)
    with pytest.raises((ValueError, RuntimeError, GuardUnavailable)):
        CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_kw: {"status": "VERIFIED"})(result)
