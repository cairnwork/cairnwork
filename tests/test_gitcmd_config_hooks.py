"""Hooks declared in git configuration (hook.<name>.command) never run under gitcmd (#737).

Each test runs only a stand-in marker command in a temporary repository: the hook touches a file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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
