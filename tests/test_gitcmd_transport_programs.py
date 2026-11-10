"""Configured transport programs do not run under gitcmd."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cairnwork import gitcmd
from cairnwork.gitcmd import run_git
from cairnwork_attest.publish import _git as _publish_git


def _git(*args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, env=env)


def _repo(path: Path, *, bare: bool = False) -> Path:
    _git("init", "-q", "--template=", "--bare" if bare else "--initial-branch=main", str(path))
    return path


def _commit(repo: Path) -> None:
    (repo / "file").write_text("data\n")
    _git("-C", str(repo), "add", "file")
    _git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")


def _program(tmp_path: Path, marker: Path, *, output: str = "") -> Path:
    program = tmp_path / "program"
    program.write_text(f'#!/bin/sh\ntouch "{marker}"\n{output}\n')
    program.chmod(0o755)
    return program


def test_configured_upload_pack_is_not_run(tmp_path: Path) -> None:
    repo, source = _repo(tmp_path / "repo"), _repo(tmp_path / "source")
    _commit(source)
    marker = tmp_path / "upload-pack-ran"
    program = _program(tmp_path, marker)
    _git("-C", str(repo), "remote", "add", "origin", str(source))
    _git("-C", str(repo), "config", "remote.origin.uploadpack", str(program))

    try:
        run_git(repo, "fetch", "origin", "HEAD")
    except gitcmd.GitCommandError:
        pass

    assert not marker.exists()


def test_configured_receive_pack_is_not_run(tmp_path: Path) -> None:
    repo, target = _repo(tmp_path / "repo"), _repo(tmp_path / "target", bare=True)
    _commit(repo)
    marker = tmp_path / "receive-pack-ran"
    program = _program(tmp_path, marker)
    _git("-C", str(repo), "remote", "add", "origin", str(target))
    _git("-C", str(repo), "config", "remote.origin.receivepack", str(program))

    try:
        run_git(repo, "push", "origin", "HEAD:refs/heads/main")
    except gitcmd.GitCommandError:
        pass

    assert not marker.exists()


def test_publish_configured_receive_pack_is_not_run(tmp_path: Path) -> None:
    repo, target = _repo(tmp_path / "repo"), _repo(tmp_path / "target", bare=True)
    _commit(repo)
    marker = tmp_path / "publish-receive-pack-ran"
    program = _program(tmp_path, marker)
    _git("-C", str(repo), "remote", "add", "origin", str(target))
    _git("-C", str(repo), "config", "remote.origin.receivepack", str(program))

    assert _publish_git(repo, "push", "origin", "HEAD:refs/heads/main").returncode == 0
    assert not marker.exists()


def test_configured_git_proxy_is_not_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    marker = tmp_path / "git-proxy-ran"
    program = _program(tmp_path, marker, output="exit 1")
    _git("-C", str(repo), "config", "core.gitProxy", f"{program} for example.invalid")
    _git("-C", str(repo), "config", "protocol.git.allow", "always")  # repo config cannot allow

    with pytest.raises(gitcmd.GitCommandError):
        run_git(repo, "fetch", "git://example.invalid/repo")

    assert not marker.exists()


def test_configured_alternate_refs_command_is_not_run(tmp_path: Path) -> None:
    repo, alternate = _repo(tmp_path / "repo"), _repo(tmp_path / "alternate")
    _commit(alternate)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(
        str(alternate / ".git" / "objects") + "\n"
    )
    marker = tmp_path / "alternate-refs-ran"
    program = _program(tmp_path, marker)
    _git("-C", str(repo), "config", "core.alternateRefsCommand", str(program))

    run_git(repo, "fetch", str(alternate), "HEAD")

    assert not marker.exists()


def test_nonstandard_remote_helper_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "remote-helper-ran"
    helper = bindir / "git-remote-probe"
    helper.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _git("-C", str(repo), "config", "protocol.probe.allow", "always")  # repo config cannot allow

    with pytest.raises(gitcmd.GitCommandError):
        run_git(repo, "fetch", "probe::address")

    assert not marker.exists()


def test_publish_nonstandard_remote_helper_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "publish-remote-helper-ran"
    helper = bindir / "git-remote-probe"
    helper.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _git("-C", str(repo), "config", "protocol.probe.allow", "always")

    assert _publish_git(repo, "fetch", "probe::address").returncode != 0
    assert not marker.exists()
