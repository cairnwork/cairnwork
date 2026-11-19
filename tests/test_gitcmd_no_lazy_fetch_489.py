"""#489: gitcmd refuses old Git and never lazily fetches a missing partial-clone blob."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from cairnwork import gitcmd, gitversion


def _git(*args, cwd=None, env=None):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, cwd=cwd, env=env
    ).stdout.strip()


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("git version 2.45.0", (2, 45, 0)),
        ("git version 2.45.0 (Apple Git-155)", (2, 45, 0)),
        ("git version 2.44.9", (2, 44, 9)),
        ("not a git version", None),
    ],
)
def test_parse_git_version(reported, expected):
    assert gitversion.parse_git_version(reported) == expected


def test_old_git_fails_closed_before_git_command(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv == ["git", "--version"]:
            return SimpleNamespace(stdout="git version 2.44.9\n", returncode=0)
        pytest.fail(f"git command ran before minimum-version check: {argv}")

    gitversion.git_version.cache_clear()
    monkeypatch.setattr(gitversion.subprocess, "run", fake_run)
    try:
        with pytest.raises(gitcmd.GitCommandError, match=r"requires git >= 2\.45"):
            gitcmd.run_git(tmp_path, "status")
        assert calls == [["git", "--version"]]
    finally:
        gitversion.git_version.cache_clear()


def test_missing_blob_in_partial_clone_is_not_fetched(tmp_path):
    version = gitversion.git_version()
    if version < (2, 45, 0):
        pytest.fail(f"this test requires git >= 2.45 for GIT_NO_LAZY_FETCH; found {version}")

    src, clone = tmp_path / "src", tmp_path / "clone"
    _git("init", "-q", str(src))
    _git("-C", str(src), "config", "uploadpack.allowFilter", "true")
    _git("-C", str(src), "config", "uploadpack.allowAnySHA1InWant", "true")
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (src / "f").write_text("target blob")
    _git("-C", str(src), "add", "f")
    _git("-C", str(src), *ident, "commit", "-q", "-m", "one")
    blob = _git("-C", str(src), "rev-parse", "HEAD:f")
    _git(
        "clone",
        "-q",
        "--filter=blob:none",
        "--no-local",
        "--no-checkout",
        f"file://{src}",
        str(clone),
    )

    assert _git("-C", str(clone), "config", "--get", "remote.origin.promisor") == "true"
    assert (
        _git("-C", str(clone), "config", "--get", "remote.origin.partialclonefilter") == "blob:none"
    )
    no_fetch_env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    absent = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", blob], capture_output=True, env=no_fetch_env
    )
    assert absent.returncode != 0, "target blob must be absent before the gitcmd check"

    with pytest.raises(gitcmd.GitCommandError):
        gitcmd.run_git(clone, "cat-file", "-e", blob)
    remains_absent = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", blob], capture_output=True, env=no_fetch_env
    )
    assert remains_absent.returncode != 0, "gitcmd fetched the missing promised blob"
