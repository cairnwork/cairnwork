"""git's ext:: transport stays off under gitcmd even when repo config allows it (#1106)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork import gitcmd
from cairnwork.gitcmd import run_git


def _init_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "--template=", str(path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    return path


@pytest.mark.parametrize("key", ["protocol.ext.allow", "protocol.allow"])
def test_repo_config_cannot_enable_ext_transport(tmp_path: Path, key: str) -> None:
    repo = _init_repo(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "config", key, "always"], check=True)
    marker = tmp_path / "MARKER"
    # ext:: splits its command on spaces; "% " is a literal space (git-remote-ext(1)).
    command = f"touch {marker}".replace(" ", "% ")
    with pytest.raises(gitcmd.GitCommandError, match="not allowed"):
        run_git(repo, "fetch", f"ext::sh -c {command}")
    assert not marker.exists()


def test_local_path_fetch_still_works(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    src = _init_repo(tmp_path / "src")
    head = run_git(src, "rev-parse", "HEAD").strip()
    run_git(repo, "fetch", "-q", str(src), "HEAD")
    assert run_git(repo, "rev-parse", "FETCH_HEAD").strip() == head
