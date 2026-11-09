"""Password prompt programs are turned off under gitcmd (#759)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork import gitcmd
from cairnwork.gitcmd import run_git


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--template=", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
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
    return repo


def test_a_configured_askpass_program_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    marker = tmp_path / "marker.txt"
    script = tmp_path / "askpass.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.askPass", str(script)], check=True)

    monkeypatch.setenv("SSH_ASKPASS", str(script))
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")

    seen_calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = gitcmd.subprocess.run

    def capturing_run(argv, **kw):
        env = kw.get("env")
        if env is not None:
            seen_calls.append((list(argv), dict(env)))
        return real_run(argv, **kw)

    monkeypatch.setattr(gitcmd.subprocess, "run", capturing_run)

    run_git(repo, "status")

    assert seen_calls, "subprocess.run was not called"
    cmd_call = next((argv, env) for argv, env in seen_calls if any("status" in arg for arg in argv))
    argv, env = cmd_call
    assert any("core.askPass=" in arg for arg in argv)
    assert "SSH_ASKPASS" not in env
    assert "SSH_ASKPASS_REQUIRE" not in env
    assert env.get("GIT_TERMINAL_PROMPT") == "0"


def test_git_honours_the_empty_askpass_override(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "core.askPass", "/bin/false"], check=True)
    out = run_git(repo, "config", "--get", "core.askPass")
    assert out.strip() == ""
