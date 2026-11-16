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


def test_judge_rejects_filter_configuration_before_candidate_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "a").write_text("a\n")
    (repo / ".gitattributes").write_text("* filter=x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "config", "filter.x.clean", "touch forbidden-marker")
    transcript = tmp_path / "t.json"
    transcript.write_text("{}")
    result = RunResult(
        0, "", tmp_path, repo, transcript, {}, base_sha=git(repo, "rev-parse", "HEAD")
    )
    with pytest.raises(ValueError, match="filter"):
        CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})(result)


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
        count = 0

        def __init__(self, _path: Path) -> None:
            pass

        def digest(self) -> str:
            type(self).count += 1
            return "a" * 64 if type(self).count == 1 else "b" * 64

    monkeypatch.setattr("experimental.pause.core_judge.TreeGuard", ChangingGuard)
    with pytest.raises(RuntimeError, match="changed R1 checkout"):
        CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})(result)


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
