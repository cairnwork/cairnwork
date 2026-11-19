"""env_smoke answers about the repo and config a real Cairnwork git run sees, not the caller's
GIT_* environment (Issue #1370): ``cairnwork.gitcmd`` strips every GIT_* variable first."""

import os
import subprocess

from scripts import env_smoke
from scripts.env_smoke import _mirror_multi_branch_reason

MULTI = "+refs/heads/*:refs/remotes/origin/*"


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def test_git_dir_in_caller_env_does_not_hide_a_multi_branch_mirror(repo, tmp_path, monkeypatch):
    _git(repo, "config", "remote.origin.fetch", MULTI)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main", "--template=")
    assert "not single-branch" in (_mirror_multi_branch_reason(repo) or "")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert "not single-branch" in (_mirror_multi_branch_reason(repo) or "")


def test_global_config_var_in_caller_env_does_not_hide_the_refspec(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(f'[remote "origin"]\n\tfetch = {MULTI}\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    assert "not single-branch" in (_mirror_multi_branch_reason(repo) or "")


def test_config_files_ignore_caller_git_config_global(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "elsewhere"))
    assert tmp_path / "elsewhere" not in env_smoke._config_files(repo)


def test_injected_config_in_caller_env_does_not_add_a_refspec(repo, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.fetch")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", MULTI)
    assert _mirror_multi_branch_reason(repo) is None
