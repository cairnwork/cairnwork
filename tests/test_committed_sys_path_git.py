"""Real temporary git repository integration tests for committed_sys_path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from experimental.committed_sys_path import check_commit
from experimental.committed_sys_path.cli import main


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return p.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "bin").mkdir()
    (repo / "bin" / "tool.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "src" / "shadow").mkdir(parents=True)
    (repo / "src" / "shadow" / "keep.py").write_text("Y = 1\n", encoding="utf-8")
    (repo / "src" / "old.py").write_text("Z = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_git_clean_accept_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clean relative ../bin edit accepts (CLI exit 0); disabled CLI exits 2."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "app.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "cand")
    cand = _git(repo, "rev-parse", "HEAD")

    assert check_commit(repo, base, cand).verdict == "ACCEPT"
    monkeypatch.delenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", raising=False)
    assert main(["--repo", str(repo), "--base", base, "--candidate", cand]) == 2
    monkeypatch.setenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", "1")
    assert main(["--repo", str(repo), "--base", base, "--candidate", cand]) == 0


def test_git_subpackage_shadow_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding src/shadow/calc/__init__.py rejects parent src/shadow (CX-1)."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "shadow" / "calc").mkdir(parents=True)
    (repo / "src" / "shadow" / "calc" / "__init__.py").write_text("X = 2\n", encoding="utf-8")
    (repo / "bin" / "runner.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'shadow'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "shadow")
    cand = _git(repo, "rev-parse", "HEAD")

    res = check_commit(repo, base, cand)
    assert res.verdict == "REJECT"
    assert res.reason == "SHADOW_IN_COMMITTED_DIR"
    monkeypatch.setenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", "1")
    assert main(["--repo", str(repo), "--base", base, "--candidate", cand]) == 1


def test_git_non_ascii_and_delete_only(tmp_path: Path) -> None:
    """Non-ASCII filename is checked (-z, CX-12); delete-only commit accepts."""
    repo, base = _init_repo(tmp_path)
    (repo / "src" / "\u00e9vil.py").write_text(
        "import sys\nsys.path.insert(0, '/tmp')\n", encoding="utf-8"
    )
    _git(repo, "add", "src/\u00e9vil.py")
    _git(repo, "commit", "-m", "nonascii")
    cand = _git(repo, "rev-parse", "HEAD")
    assert check_commit(repo, base, cand).verdict == "REJECT"

    repo2, base2 = _init_repo(tmp_path / "del")
    (repo2 / "src" / "old.py").unlink()
    _git(repo2, "add", "-u")
    _git(repo2, "commit", "-m", "delete")
    cand2 = _git(repo2, "rev-parse", "HEAD")
    assert check_commit(repo2, base2, cand2).verdict == "ACCEPT"


def test_git_symlink_dir_and_invalid_oid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidate replacing dir with symlink rejects (CX-13); invalid OID errors."""
    repo, base = _init_repo(tmp_path)
    (repo / "bin" / "tool.py").unlink()
    (repo / "bin").rmdir()
    (repo / "bin").symlink_to("src/shadow")
    (repo / "src" / "app.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bin'))\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "symlink")
    cand = _git(repo, "rev-parse", "HEAD")
    assert check_commit(repo, base, cand).verdict == "REJECT"

    assert check_commit(repo, "bad-ref", cand).verdict == "ERROR"
    monkeypatch.setenv("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH", "1")
    assert main(["--repo", str(repo), "--base", "bad-ref", "--candidate", cand]) == 1
