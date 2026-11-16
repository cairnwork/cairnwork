from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cairnwork import replay_set


class Task:
    test_commit = ""
    verifiers = (("pytest", ("python", "-m", "pytest")),)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def commits(
    tmp_path: Path,
    before: dict[str, bytes],
    after: dict[str, bytes],
    *,
    modes: dict[str, int] | None = None,
) -> tuple[Path, Task, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    for path, data in before.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    task = Task()
    task.base_commit = git(repo, "rev-parse", "HEAD")  # pyright: ignore[reportAttributeAccessIssue]
    for path in before.keys() - after.keys():
        (repo / path).unlink()
    for path, data in after.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if modes and path in modes:
            target.chmod(modes[path])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    return repo, task, git(repo, "rev-parse", "HEAD")


def record(repo: Path, task: Task, candidate: str) -> dict:
    return replay_set.capture(task, candidate, repo=repo)


def test_tree_changes_include_add_delete_binary_and_mode(tmp_path):
    repo, task, candidate = commits(
        tmp_path,
        {"gone": b"old", "binary": b"\0\x01", "exec": b"run"},
        {"binary": b"\0\x02", "exec": b"run", "new": b"added"},
        modes={"exec": 0o755},
    )
    changes = record(repo, task, candidate)["patch"]["changes"]
    by_path = {change["path"]: change for change in changes}
    assert set(by_path) == {"binary", "exec", "gone", "new"}
    assert by_path["gone"]["new"] is None and by_path["new"]["old"] is None
    assert by_path["binary"]["old"][2] != by_path["binary"]["new"][2]
    assert by_path["exec"]["old"][0] == "100644"
    assert by_path["exec"]["new"][0] == "100755"


def test_gitlink_and_symlink_are_preserved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    task = Task()
    for oid in ("1" * 40,):
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{oid},sub")
    os.symlink("target-a", repo / "link")
    git(repo, "add", "link")
    git(repo, "commit", "-qm", "base")
    task.base_commit = git(repo, "rev-parse", "HEAD")  # pyright: ignore[reportAttributeAccessIssue]
    git(repo, "update-index", "--cacheinfo", f"160000,{'3' * 40},sub")
    (repo / "link").unlink()
    os.symlink("target-b", repo / "link")
    git(repo, "add", "link")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    changes = record(repo, task, candidate)["patch"]["changes"]
    assert {item["old"][1] for item in changes} == {"commit", "blob"}
    assert {item["old"][0] for item in changes} == {"160000", "120000"}


def test_diff_settings_do_not_change_hash(tmp_path):
    repo, task, candidate = commits(tmp_path, {"a": b"old\n"}, {"a": b"new\n"})
    expected = record(repo, task, candidate)["patch"]["sha256"]
    settings = {
        "color.ui": "always",
        "diff.noprefix": "true",
        "diff.srcPrefix": "X/",
        "diff.context": "1",
        "diff.renames": "false",
        "diff.suppressBlankEmpty": "true",
        "diff.algorithm": "patience",
        "diff.external": "/bin/false",
        "diff.foo.textconv": "/bin/false",
    }
    for key, value in settings.items():
        git(repo, "config", key, value)
    (repo / ".gitattributes").write_text("a diff=foo\n")
    assert record(repo, task, candidate)["patch"]["sha256"] == expected


def test_changes_are_omitted_when_over_limit(tmp_path):
    before = {f"f{i:04}": b"old" for i in range(2600)}
    after = {path: b"new" for path in before}
    repo, task, candidate = commits(tmp_path, before, after)
    patch = record(repo, task, candidate)["patch"]
    assert "changes" not in patch
    assert len(patch["sha256"]) == 64


def test_for_status_only_records_unverified_failures(tmp_path):
    repo, task, candidate = commits(tmp_path, {"a": b"x"}, {"a": b"y"})
    task.repo = repo  # pyright: ignore[reportAttributeAccessIssue]
    for status in ("FAILED", "UNCOMPARABLE"):
        assert replay_set.for_status(status, task, candidate)["candidate_commit"] == candidate
    for status in ("VERIFIED", "REFUSED", "FAILED"):
        assert replay_set.for_status(status, task, candidate, verified=(status == "FAILED")) == {}
