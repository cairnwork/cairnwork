"""#1465: the trusted pin guard refuses a head tree that tracks Python bytecode."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/ci/pin_guard.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-f", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def setup(tmp_path: Path) -> tuple[Path, Path, str]:
    repo, trusted = tmp_path / "repo", tmp_path / "trusted"
    repo.mkdir()
    (trusted / "scripts/ci").mkdir(parents=True)
    (trusted / "scripts/ci/pin_guard_approvers.txt").write_text("owner\n", encoding="utf-8")
    git(repo, "init", "-q")
    (repo / "pkg").mkdir()
    (repo / "pkg/mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    return repo, trusted, commit(repo, "base")


def add(repo: Path, name: str, data: bytes = b"\x00bytecode") -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return commit(repo, "add " + name.replace("\n", " "))


def invoke(
    repo: Path, trusted: Path, base: str, head: str, comments: list[dict] | None = None
) -> tuple[int, str]:
    args = [sys.executable, "-I", str(SCRIPT), "--trusted", str(trusted), "--head", str(repo)]
    args += [f"--base-sha={base}", f"--head-sha={head}"]
    if comments is not None:
        comment_file = repo.parent / "comments.json"
        comment_file.write_text(json.dumps(comments), encoding="utf-8")
        args += ["--comments", str(comment_file)]
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout


BYTECODE = [
    "pkg/__pycache__/mod.cpython-313.pyc",  # unchecked-hash pyc next to a reviewed .py
    "pkg/other.pyc",  # sourceless pyc, importable as pkg.other
    "pkg/legacy.pyo",
    "pkg/__pycache__/notes.txt",
    "pkg/sub/__PYCACHE__/mod.cpython-311.pyc",
    "pkg/UPPER.PYC",
]


@pytest.mark.parametrize("name", BYTECODE)
def test_tracked_bytecode_is_refused(tmp_path: Path, name: str) -> None:
    repo, trusted, base = setup(tmp_path)
    head = add(repo, name)
    code, out = invoke(repo, trusted, base, head)
    assert code == 3
    assert out == f"PIN_GUARD BYTECODE_TRACKED {json.dumps(name)}\n"


def test_owner_approval_does_not_allow_bytecode(tmp_path: Path) -> None:
    repo, trusted, base = setup(tmp_path)
    head = add(repo, "pkg/__pycache__/mod.cpython-313.pyc")
    approval = {
        "user": {"login": "owner"},
        "body": f"PIN-APPROVE {head}\n",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    code, out = invoke(repo, trusted, base, head, [approval])
    assert (code, out.split()[1]) == (3, "BYTECODE_TRACKED")


def test_bytecode_already_on_base_is_still_refused(tmp_path: Path) -> None:
    repo, trusted, _ = setup(tmp_path)
    base = add(repo, "pkg/other.pyc")
    (repo / "pkg/mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    head = commit(repo, "unrelated change")
    code, out = invoke(repo, trusted, base, head)
    assert (code, out) == (3, 'PIN_GUARD BYTECODE_TRACKED "pkg/other.pyc"\n')


def test_gitlink_is_refused(tmp_path: Path) -> None:
    # ls-tree -r does not descend into a submodule, so bytecode inside one would be hidden.
    repo, trusted, base = setup(tmp_path)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},vendor/lib")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "gitlink")
    head = git(repo, "rev-parse", "HEAD")
    assert invoke(repo, trusted, base, head) == (3, 'PIN_GUARD BYTECODE_TRACKED "vendor/lib"\n')


def test_bytecode_name_with_newline_prints_one_line(tmp_path: Path) -> None:
    repo, trusted, base = setup(tmp_path)
    head = add(repo, "pkg/x\n::error::forged.pyc")
    code, out = invoke(repo, trusted, base, head)
    assert code == 3
    assert out.count("\n") == 1 and "\n::error::" not in out


@pytest.mark.parametrize(
    "name",
    ["pkg/pyc_helper.py", "docs/pycache.md", "pkg/__pycache___x/mod.py", "pkg/mod.pycx", "a.pyi"],
)
def test_lookalike_names_pass(tmp_path: Path, name: str) -> None:
    repo, trusted, base = setup(tmp_path)
    head = add(repo, name, b"text\n")
    assert invoke(repo, trusted, base, head) == (0, "PIN_GUARD PIN_GUARD_OK\n")


def test_unreadable_head_fails_closed(tmp_path: Path) -> None:
    repo, trusted, base = setup(tmp_path)
    missing = "f" * 40
    assert invoke(repo, trusted, base, missing) == (2, "PIN_GUARD PIN_GUARD_INCOMPLETE\n")
