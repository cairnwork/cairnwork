"""#489: gitcmd probes and enforces Git's no-lazy-fetch option."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import gitcmd, gitversion


def _git(*args, cwd=None, env=None):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, cwd=cwd, env=env
    ).stdout.strip()


def _reset_git_detection() -> None:
    gitversion.require_no_lazy_fetch.cache_clear()
    gitversion.git_executable.cache_clear()


def test_option_probe_uses_absolute_git_path_and_caches_it(monkeypatch):
    calls = []
    which_calls = []

    def fake_which(name):
        which_calls.append(name)
        return "/tools/git"

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    _reset_git_detection()
    monkeypatch.setattr(gitversion.shutil, "which", fake_which)
    monkeypatch.setattr(gitversion.subprocess, "run", fake_run)
    try:
        gitversion.require_no_lazy_fetch()
        gitversion.require_no_lazy_fetch()
        assert calls == [["/tools/git", "--no-lazy-fetch", "version"]]
        assert which_calls == ["git"]
    finally:
        _reset_git_detection()


@pytest.mark.parametrize("failure", ["missing", "unsupported"])
def test_probe_failure_fails_closed_before_git_command(monkeypatch, tmp_path, failure):
    calls = []

    def fake_which(name):
        return None if failure == "missing" else "/tools/old-git"

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=129)

    _reset_git_detection()
    monkeypatch.setattr(gitversion.shutil, "which", fake_which)
    monkeypatch.setattr(gitversion.subprocess, "run", fake_run)
    try:
        with pytest.raises(gitcmd.GitCommandError, match="--no-lazy-fetch"):
            gitcmd.run_git(tmp_path, "status")
        if failure == "missing":
            assert calls == []
        else:
            assert calls == [["/tools/old-git", "--no-lazy-fetch", "version"]]
    finally:
        _reset_git_detection()


def test_run_git_uses_same_resolved_absolute_git_path(monkeypatch, tmp_path):
    calls = []

    def fake_which(name):
        return "/tools/git"

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "version" in argv:
            return SimpleNamespace(returncode=0)
        if "config" in argv:
            return SimpleNamespace(returncode=0, stdout=b"")
        return SimpleNamespace(stdout="ok")

    _reset_git_detection()
    monkeypatch.setattr(gitversion.shutil, "which", fake_which)
    monkeypatch.setattr(gitversion.subprocess, "run", fake_run)
    try:
        assert gitcmd.run_git(tmp_path, "status") == "ok"
        assert all(argv[0] == "/tools/git" for argv in calls)
        assert calls[0] == ["/tools/git", "--no-lazy-fetch", "version"]
    finally:
        _reset_git_detection()


def _partial_clone(src: Path, clone: Path, blob: str) -> None:
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
    absent = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", blob],
        capture_output=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    assert absent.returncode != 0, "target blob must be absent before the check"


def test_missing_blob_is_not_fetched_and_control_without_variable_fetches(tmp_path):
    executable = gitversion.git_executable()
    probe = subprocess.run(
        [executable, "--no-lazy-fetch", "version"], capture_output=True, check=False
    )
    if probe.returncode != 0:
        pytest.fail(f"test requires git supporting --no-lazy-fetch: {probe.stderr!r}")

    src = tmp_path / "src"
    _git("init", "-q", str(src))
    _git("-C", str(src), "config", "uploadpack.allowFilter", "true")
    _git("-C", str(src), "config", "uploadpack.allowAnySHA1InWant", "true")
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (src / "f").write_text("target blob")
    _git("-C", str(src), "add", "f")
    _git("-C", str(src), *ident, "commit", "-q", "-m", "one")
    blob = _git("-C", str(src), "rev-parse", "HEAD:f")

    protected, control = tmp_path / "protected", tmp_path / "control"
    _partial_clone(src, protected, blob)
    _partial_clone(src, control, blob)

    no_fetch_env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    with pytest.raises(gitcmd.GitCommandError):
        gitcmd.run_git(protected, "cat-file", "-e", blob)
    remains_absent = subprocess.run(
        [executable, "-C", str(protected), "cat-file", "-e", blob],
        capture_output=True,
        env=no_fetch_env,
    )
    assert remains_absent.returncode != 0, "gitcmd fetched the missing promised blob"

    lazy_fetch = subprocess.run(
        [executable, "-C", str(control), "cat-file", "-e", blob],
        capture_output=True,
        env={k: v for k, v in os.environ.items() if k != "GIT_NO_LAZY_FETCH"},
    )
    assert lazy_fetch.returncode == 0, "control Git did not fetch the missing promised blob"
