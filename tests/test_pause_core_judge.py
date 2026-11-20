"""The Core judge commits and verifies an isolated copy of R1."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult
from experimental.pause.guard import TreeGuard


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_judge_verifies_copy_without_changing_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "old.py").write_text("old\n")
    (repo / "changed.py").write_text("before\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "old.py").unlink()
    (repo / "new.py").write_text("new\n")
    (repo / "new.py").chmod(0o755)
    (repo / "changed.py").write_text("after\n")
    transcript = tmp_path / "transcript.json"
    transcript.write_text("{}")
    result = RunResult(0, "", tmp_path, repo, transcript, {}, base_sha=base)
    before = TreeGuard(repo).digest()
    seen: dict[str, Any] = {}

    def verify(_intake: Any, **kwargs: Any) -> dict[str, str]:
        seen.update(kwargs)
        seen["new_mode"] = (kwargs["repo"] / "new.py").stat().st_mode
        seen["old_exists"] = (kwargs["repo"] / "old.py").exists()
        seen["candidate_new"] = git(kwargs["repo"], "show", f"{kwargs['candidate']}:new.py")
        seen["candidate_changed"] = git(kwargs["repo"], "show", f"{kwargs['candidate']}:changed.py")
        seen["candidate_bytes"] = subprocess.run(
            [
                "git",
                "-C",
                str(kwargs["repo"]),
                "cat-file",
                "blob",
                f"{kwargs['candidate']}:changed.py",
            ],
            check=True,
            capture_output=True,
        ).stdout
        return {"status": "VERIFIED"}

    judge = CoreJudge(tmp_path / "state", (), verify=verify)
    verdict = judge(result)
    assert verdict.tree_sha256 == before == TreeGuard(repo).digest()
    assert set(seen["allowed_files"]) == {"old.py", "new.py", "changed.py"}
    assert seen["new_mode"] & 0o111
    assert not seen["old_exists"]
    assert seen["candidate_new"] == "new"
    assert seen["candidate_changed"] == "after"
    assert seen["candidate_bytes"] == b"after\n"


def test_judge_does_not_run_copied_post_commit_hook(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("a\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\nprintf ran > {marker}\n")
    hook.chmod(0o755)
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(
        0, "", tmp_path, repo, transcript, {}, base_sha=git(repo, "rev-parse", "HEAD")
    )
    CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})(result)
    assert not marker.exists()


def test_judge_does_not_read_or_run_r1_filter_configuration(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("a\n")
    (repo / ".gitattributes").write_text("* filter=x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    marker = tmp_path / "forbidden-marker"
    git(repo, "config", "filter.x.clean", f"touch {marker}; cat")
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(
        0, "", tmp_path, repo, transcript, {}, base_sha=git(repo, "rev-parse", "HEAD")
    )
    verdict = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})(
        result
    )
    assert verdict.status == "FAILED" and not marker.exists()


def test_allowed_files_use_pinned_base_including_r1_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src/a.py").write_text("R1 committed\n")
    git(repo, "add", "src/a.py")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "R1")
    observed: dict[str, Any] = {}
    item = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    CoreJudge(
        tmp_path / "state",
        (),
        verify=lambda _intake, **kwargs: observed.update(kwargs) or {"status": "FAILED"},
    )(item)
    assert set(observed["allowed_files"]) == {"src/a.py"}


def test_judge_requires_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    item = RunResult(0, "", tmp_path, repo, transcript, {}, base_sha=git(repo, "rev-parse", "HEAD"))
    with pytest.raises(ValueError):
        CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {})(item)


def test_judge_rejects_changed_checkout_digest(tmp_path: Path, monkeypatch: Any) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(
        0, "", tmp_path, repo, transcript, {}, base_sha=git(repo, "rev-parse", "HEAD")
    )

    class ChangingGuard:
        def __init__(self, _path: Path, **options: Any) -> None:
            self.exclude_git = options.get("exclude_git", False)

        def digest(self) -> str:
            return "a" * 64 if self.exclude_git else "b" * 64

        def digest_pair(self, **_options: Any) -> tuple[str, str]:
            return "a" * 64, "a" * 64

    monkeypatch.setattr("experimental.pause.core_judge.TreeGuard", ChangingGuard)
    with pytest.raises(RuntimeError, match="changed R1 checkout"):
        CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})(result)


def test_judge_rejects_copy_that_does_not_match_entry_tree(
    tmp_path: Path, monkeypatch: Any
) -> None:
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
    entry_digest = TreeGuard(repo).digest()
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(0, "", tmp_path, repo, transcript, {}, base_sha=base)
    seen: list[str] = []
    from experimental.pause import core_judge

    original_clone = core_judge._clone
    original_candidate_digest = core_judge._candidate_tree_digest

    def clone_with_transient_change(source: Path, destination: Path) -> None:
        (repo / "a").write_text("B\n")
        original_clone(source, destination)

    def verify(_intake: Any, **kwargs: Any) -> dict[str, str]:
        seen.append(git(kwargs["repo"], "show", f"{kwargs['candidate']}:a"))
        return {"status": "VERIFIED"}

    def candidate_digest_then_restore(
        candidate_repo: Path, candidate: str, excluded: frozenset[str]
    ) -> str:
        digest = original_candidate_digest(candidate_repo, candidate, excluded)
        (repo / "a").write_text("A\n")
        return digest

    monkeypatch.setattr(core_judge, "_clone", clone_with_transient_change)
    monkeypatch.setattr(core_judge, "_candidate_tree_digest", candidate_digest_then_restore)
    try:
        with pytest.raises(
            ValueError, match="Core candidate filesystem tree differs from R1 judge-entry tree"
        ):
            CoreJudge(tmp_path / "state", (), verify=verify)(result)
    finally:
        (repo / "a").write_text("A\n")
    assert seen == []
    assert TreeGuard(repo).digest() == entry_digest


def test_judge_takes_base_from_runner_not_from_r1_record(tmp_path: Path) -> None:
    """A base_sha in R1's own record is ignored: without the runner's pinned base, no verdict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    head = git(repo, "rev-parse", "HEAD")
    calls: list[Any] = []
    judge = CoreJudge(tmp_path / "state", (), verify=lambda *a, **k: calls.append(k) or {})
    result = RunResult(0, "", tmp_path, repo, None, {"base_sha": head})
    with pytest.raises(ValueError):
        judge(result)
    assert calls == []


def test_judge_ignores_callers_global_git_filter(tmp_path: Path, monkeypatch: Any) -> None:
    """A clean filter from the caller's global git config must not run on the candidate."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a").write_text("a\n")
    git(repo, "add", "a")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / ".gitattributes").write_text("* filter=x\n")
    (repo / "a").write_text("candidate\n")
    marker = tmp_path / "filter-ran"
    config = tmp_path / "global.gitconfig"
    config.write_text(f'[filter "x"]\n\tclean = "touch {marker}; cat"\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    seen: list[Any] = []

    def verify(*_a: Any, **k: Any) -> dict[str, str]:
        seen.append(git(k["repo"], "show", f"{k['candidate']}:a"))
        return {"status": "FAILED"}

    result = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    CoreJudge(tmp_path / "state", (), verify=verify)(result)
    assert not marker.exists()
    assert seen == ["candidate"]


def test_judge_rejects_excluded_directory_file_added_after_entries_snapshot(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An excluded directory cannot gain a file after source entries were enumerated."""
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
    original_entries = core_judge._entries
    original_copy = core_judge._copy_working_tree
    entries_calls = 0

    def entries_then_add(path: Path):
        nonlocal entries_calls
        entries = original_entries(path)
        entries_calls += 1
        if path == repo and entries_calls == 1:
            (repo / "empty-after-delete" / "late").write_text("late\n")
        return entries

    def copy_then_remove(source: Path, target: Path):
        copied = original_copy(source, target)
        (repo / "empty-after-delete" / "late").unlink(missing_ok=True)
        return copied

    monkeypatch.setattr(core_judge, "_entries", entries_then_add)
    monkeypatch.setattr(core_judge, "_copy_working_tree", copy_then_remove)
    with pytest.raises(ValueError):
        CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "VERIFIED"})(result)
    assert entries_calls >= 1


def test_judge_accepts_unchanged_excluded_empty_directory(tmp_path: Path) -> None:
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
    result = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    verdict = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "VERIFIED"})(
        result
    )
    assert verdict.status == "VERIFIED"


def test_judge_preserves_failed_core_status_and_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("candidate\n")
    before = TreeGuard(repo).digest()
    result = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    verdict = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})(
        result
    )
    assert verdict.status == "FAILED"
    assert verdict.tree_sha256 == before == TreeGuard(repo).digest()


def test_judge_verifies_add_delete_mode_mix_with_unchanged_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "delete").write_text("old\n")
    (repo / "keep").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "delete").unlink()
    (repo / "keep").write_text("new\n")
    (repo / "add").write_text("added\n")
    (repo / "add").chmod(0o755)
    before = TreeGuard(repo).digest()
    result = RunResult(0, "", tmp_path, repo, None, {}, base_sha=base)
    verdict = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "VERIFIED"})(
        result
    )
    assert verdict.status == "VERIFIED"
    assert verdict.tree_sha256 == before == TreeGuard(repo).digest()


def test_import_paths_reach_verifier_as_posix_strings(tmp_path: Path) -> None:
    """verify_candidate wants str import paths; a Path there is a BAD_IMPORT_PATH refusal."""
    judge = CoreJudge(
        tmp_path / "state",
        (),
        verify=lambda *_a, **_k: {"status": "VERIFIED"},
        import_paths=("src", Path("lib/sub")),
    )
    assert judge.import_paths == ("src", "lib/sub")
    assert all(type(path) is str for path in judge.import_paths)
