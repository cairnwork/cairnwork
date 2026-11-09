"""Password prompt programs are turned off under gitcmd (#759)."""

from __future__ import annotations

import os
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


def _askpass_script(tmp_path: Path, marker: Path) -> Path:
    script = tmp_path / "askpass.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\necho x\n")
    script.chmod(0o755)
    return script


def test_a_configured_askpass_program_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git really asks for a password here (``credential fill`` with no helper), so on the
    old code the configured askpass program runs and answers; now git may not prompt at all."""
    repo = _init_repo(tmp_path)
    marker = tmp_path / "marker.txt"
    script = _askpass_script(tmp_path, marker)
    subprocess.run(["git", "-C", str(repo), "config", "core.askPass", str(script)], check=True)
    monkeypatch.setenv("SSH_ASKPASS", str(script))
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    request = b"protocol=https\nhost=example.invalid\n\n"
    with pytest.raises(gitcmd.GitCommandError):  # no helper and no prompt: git cannot answer
        gitcmd.run_git_bytes(repo, "-c", "credential.helper=", "credential", "fill", stdin=request)
    assert not marker.exists()


def test_the_child_environment_has_no_askpass_and_no_terminal_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("SSH_ASKPASS", "/bin/false")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    seen: list[tuple[list[str], dict[str, str]]] = []
    real_run = gitcmd.subprocess.run

    def capturing_run(argv, **kw):
        seen.append((list(argv), dict(kw.get("env") or {})))
        return real_run(argv, **kw)

    monkeypatch.setattr(gitcmd.subprocess, "run", capturing_run)
    run_git(repo, "status")
    argv, env = next((a, e) for a, e in seen if "status" in a)
    assert "core.askPass=" in argv
    assert "SSH_ASKPASS" not in env and "SSH_ASKPASS_REQUIRE" not in env
    assert env.get("GIT_TERMINAL_PROMPT") == "0"


def test_git_honours_the_empty_askpass_override(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "core.askPass", "/bin/false"], check=True)
    out = run_git(repo, "config", "--get", "core.askPass")
    assert out.strip() == ""


def test_publish_git_has_no_askpass_and_no_terminal_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cairnwork_attest.publish builds its own git environment: it drops askpass too (#759)."""
    from cairnwork_attest import publish

    repo = _init_repo(tmp_path)
    monkeypatch.setenv("SSH_ASKPASS", "/bin/false")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    seen: list[tuple[list[str], dict[str, str]]] = []
    real_run = publish.subprocess.run

    def capturing_run(argv, **kw):
        seen.append((list(argv), dict(kw.get("env") or {})))
        return real_run(argv, **kw)

    monkeypatch.setattr(publish.subprocess, "run", capturing_run)
    assert publish._git(repo, "status").returncode == 0
    argv, env = next((a, e) for a, e in seen if "status" in a)
    assert "core.askPass=" in argv
    assert "SSH_ASKPASS" not in env and "SSH_ASKPASS_REQUIRE" not in env
    assert env.get("GIT_TERMINAL_PROMPT") == "0"


def _ssh_marker(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "ssh-argv"
    ssh = bindir / "ssh"
    ssh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{marker}"\nexit 255\n')
    ssh.chmod(0o755)
    return bindir, marker


def test_network_fetch_uses_noninteractive_ssh_unless_operator_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "ssh://example.invalid/x.git"],
        check=True,
    )
    bindir, marker = _ssh_marker(tmp_path)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    with pytest.raises(gitcmd.GitCommandError):
        run_git(repo, "fetch", "origin", timeout=gitcmd.NETWORK_TIMEOUT_SECONDS)
    assert "-o\nBatchMode=yes" in marker.read_text()


def test_network_fetch_does_not_rewrite_operator_ssh_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "ssh://example.invalid/x.git"],
        check=True,
    )
    marker = tmp_path / "configured-ssh-argv"
    ssh = tmp_path / "configured-ssh"
    ssh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{marker}"\nexit 255\n')
    ssh.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.sshCommand", str(ssh)], check=True)
    with pytest.raises(gitcmd.GitCommandError):
        run_git(repo, "fetch", "origin", timeout=gitcmd.NETWORK_TIMEOUT_SECONDS)
    assert marker.exists()
    assert "BatchMode" not in marker.read_text()


def test_publish_network_fetch_uses_noninteractive_ssh_unless_operator_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cairnwork_attest import publish

    repo = _init_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "ssh://example.invalid/x.git"],
        check=True,
    )
    bindir, marker = _ssh_marker(tmp_path)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    publish._git(repo, "fetch", "origin")
    assert "-o\nBatchMode=yes" in marker.read_text()


def test_publish_network_fetch_does_not_rewrite_operator_ssh_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cairnwork_attest import publish

    repo = _init_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "ssh://example.invalid/x.git"],
        check=True,
    )
    marker = tmp_path / "configured-ssh-argv"
    ssh = tmp_path / "configured-ssh"
    ssh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{marker}"\nexit 255\n')
    ssh.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.sshCommand", str(ssh)], check=True)
    publish._git(repo, "fetch", "origin")
    assert marker.exists()
    assert "BatchMode" not in marker.read_text()
