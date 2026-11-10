"""Hooks declared in git configuration (hook.<name>.command) never run under gitcmd (#737).

Each test runs only a stand-in marker command in a temporary repository: the hook touches a file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cairnwork import gitcmd
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork_attest.publish import _git


def _repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo, marker = tmp_path / "r", tmp_path / "fired"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM="1")
    for args in (
        ["init", "-q", "--template=", str(repo)],
        ["-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
         "--allow-empty", "-m", "a"],
    ):  # fmt: skip
        subprocess.run(["git", *args], check=True, env=env, capture_output=True)
    head = run_git(repo, "rev-parse", "HEAD").strip()
    return repo, marker, head


def _declare(repo: Path, name: str, marker: Path) -> None:
    for key, value in (("event", "reference-transaction"), ("command", f"touch {marker}")):
        subprocess.run(["git", "-C", str(repo), "config", f"hook.{name}.{key}", value], check=True)


def test_the_stand_in_hook_fires_without_the_option(tmp_path: Path) -> None:
    repo, marker, head = _repo(tmp_path)
    _declare(repo, "mark", marker)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), "update-ref", "refs/x", head],
        check=True,
    )
    assert marker.exists()  # the setup is real: core.hooksPath alone does not stop it


@pytest.mark.parametrize("name", ["mark", "Two.Dots.x", "with space"])
def test_a_repository_declared_hook_does_not_run(tmp_path: Path, name: str) -> None:
    repo, marker, head = _repo(tmp_path)
    _declare(repo, name, marker)
    run_git(repo, "update-ref", "refs/x", head)
    assert run_git(repo, "rev-parse", "refs/x").strip() == head and not marker.exists()


def test_a_globally_declared_hook_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, marker, head = _repo(tmp_path)
    (tmp_path / ".gitconfig").write_text(
        f'[hook "glob"]\n\tevent = reference-transaction\n\tcommand = touch {marker}\n'
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    run_git(repo, "update-ref", "refs/x", head)
    assert not marker.exists()


def test_publish_drops_a_hook_declared_in_kept_config_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, marker, head = _repo(tmp_path)
    pairs = {"COUNT": "2", "KEY_0": "hook.env.event", "VALUE_0": "reference-transaction",
             "KEY_1": "hook.env.command", "VALUE_1": f"touch {marker}"}  # fmt: skip
    for k, v in pairs.items():
        monkeypatch.setenv(f"GIT_CONFIG_{k}", v)
    assert _git(repo, "update-ref", "refs/x", head).returncode == 0
    assert not marker.exists()


def test_a_hook_name_with_an_equals_sign_is_refused(tmp_path: Path) -> None:
    repo, marker, head = _repo(tmp_path)
    _declare(repo, "a=b", marker)
    with pytest.raises(GitCommandError):
        run_git(repo, "update-ref", "refs/x", head)
    proc = _git(repo, "update-ref", "refs/y", head)
    assert proc.returncode != 0 and "'='" in proc.stderr
    assert not marker.exists()


def _declare_bytes(repo: Path, name: bytes, marker: Path) -> None:
    for key, value in (
        (b"event", b"reference-transaction"),
        (b"command", b"touch " + bytes(marker)),
    ):
        subprocess.run([b"git", b"-C", bytes(repo), b"config", b"hook." + name + b"." + key, value],
                       check=True)  # fmt: skip


@pytest.mark.parametrize("name", [b"", b"n\xff", b"caf\xe9"])
@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_an_empty_or_non_utf8_name_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: bytes, locale: str
) -> None:
    repo, marker, head = _repo(tmp_path)
    _declare_bytes(repo, name, marker)
    monkeypatch.setenv("LC_ALL", locale)
    run_git(repo, "update-ref", "refs/x", head)
    assert _git(repo, "update-ref", "refs/y", head).returncode == 0
    assert not marker.exists()


@pytest.mark.parametrize("name", ["", "\udcff"])  # "\udcff" reaches git as the byte 0xFF
def test_publish_drops_an_empty_or_non_utf8_name_from_kept_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    repo, marker, head = _repo(tmp_path)
    pairs = {"COUNT": "2", "KEY_0": f"hook.{name}.event", "VALUE_0": "reference-transaction",
             "KEY_1": f"hook.{name}.command", "VALUE_1": f"touch {marker}"}  # fmt: skip
    for k, v in pairs.items():
        monkeypatch.setenv(f"GIT_CONFIG_{k}", v)
    assert _git(repo, "update-ref", "refs/x", head).returncode == 0
    assert not marker.exists()


def test_hash_blob_turns_the_declared_hooks_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, marker, _ = _repo(tmp_path)
    _declare(repo, "mark", marker)
    seen: list[list[str]] = []
    real = gitcmd.subprocess.run
    monkeypatch.setattr(gitcmd.subprocess, "run", lambda a, **k: seen.append(a) or real(a, **k))
    gitcmd.hash_blob(repo, b"data\n")
    argv = next(a for a in seen if "hash-object" in a)
    assert "hook.mark.enabled=false" in argv


def test_a_listing_that_cannot_run_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, marker, head = _repo(tmp_path)
    real = gitcmd.subprocess.run

    def no_listing(argv, **kw):  # the listing times out; every other call is real
        if "--list" in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        return real(argv, **kw)

    monkeypatch.setattr(gitcmd.subprocess, "run", no_listing)
    with pytest.raises(GitCommandError, match="hook listing"):
        run_git(repo, "update-ref", "refs/x", head)
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", "refs/x"],
                          capture_output=True).returncode != 0  # fmt: skip


def test_a_config_listing_returning_failure_refuses_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, marker, head = _repo(tmp_path)
    _declare(repo, "mark", marker)
    real = gitcmd.subprocess.run

    def failed_listing(argv, **kwargs):
        if "--list" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"config unavailable")
        return real(argv, **kwargs)

    monkeypatch.setattr(gitcmd.subprocess, "run", failed_listing)
    with pytest.raises(GitCommandError, match="hook listing"):
        run_git(repo, "update-ref", "refs/x", head)
    assert not marker.exists()
