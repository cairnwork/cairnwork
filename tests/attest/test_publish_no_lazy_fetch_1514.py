"""publish._git, like gitcmd, never lets git fetch a missing object by itself (#1514, #489), runs
the one resolved git, and drops SSH_ASKPASS (#759)."""

import os
import subprocess

import pytest
from conftest import git

from cairnwork import gitversion
from cairnwork_attest import publish

pytestmark = pytest.mark.usefixtures("production_git_env")


def _partial_clone(src, clone):
    subprocess.run(
        ["git", "clone", "-q", "--filter=blob:none", "--no-local", "--no-checkout",
         f"file://{src}", str(clone)],
        check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def promised_blob(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "uploadpack.allowFilter", "true")
    git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(src, "config", "user.email", "t@t.invalid")
    git(src, "config", "user.name", "t")
    (src / "f").write_text("target blob")
    git(src, "add", "f")
    git(src, "commit", "-q", "-m", "one")
    return src, git(src, "rev-parse", "HEAD:f")


def test_a_missing_promised_blob_is_not_fetched(promised_blob, tmp_path):
    src, blob = promised_blob
    clone = tmp_path / "clone"
    _partial_clone(src, clone)
    got = publish._git(clone, "cat-file", "-e", blob)
    assert got.returncode != 0, "publish._git fetched the missing promised blob"
    again = subprocess.run(
        [gitversion.git_executable(), "-C", str(clone), "cat-file", "-e", blob],
        capture_output=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    assert again.returncode != 0, "the blob is present after publish._git: it was fetched"


def test_the_child_sees_no_lazy_fetch_and_no_askpass(repo, monkeypatch):
    monkeypatch.setenv("SSH_ASKPASS", "/bin/false")
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "0")  # the caller cannot switch it back on
    seen = {}
    real = subprocess.run

    def spy(argv, **kwargs):
        if "env" in kwargs and "rev-parse" in argv:
            seen.update(kwargs["env"])
            seen["argv0"] = argv[0]
        return real(argv, **kwargs)

    monkeypatch.setattr(publish.subprocess, "run", spy)
    assert publish._git(repo, "rev-parse", "HEAD").returncode == 0
    assert seen["GIT_NO_LAZY_FETCH"] == "1"
    assert "SSH_ASKPASS" not in seen
    assert seen["argv0"] == gitversion.git_executable()


def test_a_git_without_the_option_fails_closed_before_running(repo, monkeypatch):
    def refuse():
        raise gitversion.GitVersionError("requires Git supporting global --no-lazy-fetch")

    monkeypatch.setattr(publish, "require_no_lazy_fetch", refuse)
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda *a, **k: calls.append(a))
    got = publish._git(repo, "rev-parse", "HEAD")
    assert got.returncode != 0 and "no-lazy-fetch" in got.stderr
    assert calls == []
