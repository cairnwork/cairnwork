from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork import replay_set


class Task:
    base_commit: str
    repo: Path
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


def test_verified_and_refused_have_no_replay_set():
    assert replay_set.for_status("VERIFIED", None, "x") == {}
    assert replay_set.for_status("REFUSED", None, "x") == {}


def test_argv_secret_value_in_separate_element_is_masked():
    for flag, value in [("--token", "abc123"), ("--password", "hunter2"), ("--api-key", "zzz"),
                        ("--secret", "ss"), ("--passwd", "p"), ("--key", "k"), ("--pwd", "q"),
                        ("--private-key", "pk"), ("--auth", "a")]:  # fmt: skip
        out = replay_set._safe_argv(("x", flag, value), "/h")
        assert out == ["x", flag, "<redacted>"], flag
    assert replay_set._safe_argv(("x", "--token=abc123"), "/h") == ["x", "--token=<redacted>"] or (
        "abc123" not in repr(replay_set._safe_argv(("x", "--token=abc123"), "/h"))
    )
    plain = ("python", "-m", "pytest", "-q", "--tb", "short", "--keep-going")
    assert replay_set._safe_argv(plain, "/h") == list(plain)


def test_home_is_replaced_by_placeholder():
    home = str(Path.home().resolve())
    out = replay_set._safe_argv((home + "/work", "--cfg=" + home + "/x"), home)
    assert out == ["<home>/work", "--cfg=<home>/x"]


def test_for_status_keeps_failed_and_uncomparable_only(tmp_path):
    repo, task, candidate = _repo(tmp_path)
    task.repo = repo
    for status in ("FAILED", "UNCOMPARABLE"):
        assert replay_set.for_status(status, task, candidate)["candidate_commit"] == candidate
    for status in ("VERIFIED", "REFUSED"):
        assert replay_set.for_status(status, task, candidate) == {}


def test_for_status_swallows_a_task_without_repo():
    class Bad:
        @property
        def repo(self):
            raise RuntimeError("no repo")

    assert replay_set.for_status("FAILED", Bad(), "x") == {"replay_set_error": "RuntimeError"}
