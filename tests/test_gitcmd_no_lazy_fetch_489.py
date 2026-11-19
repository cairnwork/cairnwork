"""#489: a git command run through gitcmd never lazily fetches from a partial clone's remote."""

import os
import subprocess

import pytest

from cairnwork.gitcmd import GitCommandError, run_git


def _git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, cwd=cwd
    ).stdout.strip()


def test_missing_object_in_a_partial_clone_is_not_fetched(tmp_path):
    src, clone = tmp_path / "src", tmp_path / "clone"
    _git("init", "-q", str(src))
    _git("-C", str(src), "config", "uploadpack.allowFilter", "true")
    _git("-C", str(src), "config", "uploadpack.allowAnySHA1InWant", "true")
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (src / "f").write_text("x")
    _git("-C", str(src), "add", "f")
    _git("-C", str(src), *ident, "commit", "-q", "-m", "one")
    branch = _git("-C", str(src), "branch", "--show-current")
    (src / "f").write_text("y")
    _git("-C", str(src), "add", "f")
    _git("-C", str(src), *ident, "commit", "-q", "-m", "two")
    target = _git("-C", str(src), "rev-parse", "HEAD")
    _git("-C", str(src), "update-ref", "refs/hidden/t", target)
    _git("-C", str(src), "reset", "-q", "--hard", "HEAD~1")
    _git("-C", str(src), "update-ref", "refs/hidden/keep", "HEAD")
    _git("clone", "-q", "--filter=blob:none", "--no-local", f"file://{src}", str(clone))
    assert branch
    with pytest.raises(GitCommandError):
        run_git(clone, "cat-file", "-e", f"{target}^{{commit}}")
    present = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", target],
        capture_output=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},  # the check itself must not fetch
    ).returncode
    assert present != 0  # the object was not fetched as a side effect
