from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork import replay_set


class Task:
    base_commit: str
    test_commit = ""
    verifiers = (("pytest", ("python", "-m", "pytest", "--token=secret-value")),)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Task, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "sample.py").write_text("value = 1\n")
    _git(repo, "add", "sample.py")
    _git(repo, "commit", "-qm", "base")
    task = Task()
    task.base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "sample.py").write_text("value = 2\n")
    _git(repo, "commit", "-qam", "candidate")
    return repo, task, _git(repo, "rev-parse", "HEAD")


def test_capture_has_replay_shape_and_patch_hash(tmp_path):
    repo, task, candidate = _repo(tmp_path)
    record = replay_set.capture(task, candidate, repo=repo)
    assert set(record) == {
        "base",
        "candidate_commit",
        "candidate_patch_sha256",
        "test_commit",
        "verifiers",
        "core_build",
    }
    assert record["base"] == task.base_commit
    assert record["candidate_commit"] == candidate
    assert record["test_commit"] is None
    assert record["candidate_patch_sha256"]
    assert record["core_build"]["version"]
    assert record["verifiers"][0][1][-1] == "--token=<redacted>"
    assert "secret-value" not in repr(record)
    # Mutant guard: omitting the patch identity must fail this assertion.
    assert "candidate_patch_sha256" in record


def test_patch_hash_changes_with_candidate(tmp_path):
    repo, task, first = _repo(tmp_path)
    first_hash = replay_set.capture(task, first, repo=repo)["candidate_patch_sha256"]
    (repo / "sample.py").write_text("value = 3\n")
    _git(repo, "commit", "-qam", "next candidate")
    second = _git(repo, "rev-parse", "HEAD")
    second_hash = replay_set.capture(task, second, repo=repo)["candidate_patch_sha256"]
    assert first_hash != second_hash


def test_capture_failure_is_explicit_and_does_not_change_verdict(tmp_path, monkeypatch):
    repo, task, candidate = _repo(tmp_path)
    verdict_before = "FAILED"

    def fail(*args, **kwargs):
        raise RuntimeError("capture unavailable")

    monkeypatch.setattr(replay_set, "run_git_bytes", fail)
    record = replay_set.capture(task, candidate, repo=repo)
    verdict_after = "FAILED"
    assert record == {"replay_set_error": "RuntimeError"}
    assert verdict_after == verdict_before


def test_verified_and_refused_have_no_replay_set():
    assert replay_set.for_status("VERIFIED", None, "x") == {}
    assert replay_set.for_status("REFUSED", None, "x") == {}
