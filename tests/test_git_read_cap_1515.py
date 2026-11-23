"""#1515 family: the verifier's reads of candidate git objects are held to a byte cap."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import cairnwork.git_read_cap as cap
from cairnwork import candidate_env, candidate_shadow, candidate_sys_path, tamper_scan_plugin
from cairnwork.gitcmd import GitCommandError

SMALL_CAP = 4096


def _git(repo: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "small.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "big.py").write_text("y = '" + "a" * (SMALL_CAP * 2) + "'\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "c")
    return tmp_path


@pytest.fixture(autouse=True)
def small_cap(monkeypatch):
    monkeypatch.setattr(cap, "MAX_GIT_READ", SMALL_CAP)


def _oid(repo: Path, path: str) -> str:
    return _git(repo, "rev-parse", f"HEAD:{path}")


def test_the_helper_refuses_an_oversized_read_and_passes_a_normal_one(repo):
    assert cap.read_git_bytes(repo, "cat-file", "blob", "HEAD:pkg/small.py") == b"x = 1\n"
    with pytest.raises(GitCommandError):
        cap.read_git_bytes(repo, "cat-file", "blob", "HEAD:pkg/big.py")


def test_candidate_shadow_batch_read_is_capped(repo):
    small, big = _oid(repo, "pkg/small.py"), _oid(repo, "pkg/big.py")
    assert candidate_shadow._read_source_blobs(repo, [("a", "b", small)]) == {small: b"x = 1\n"}
    with pytest.raises(GitCommandError):
        candidate_shadow._read_source_blobs(repo, [("a", "b", big)])


def test_candidate_shadow_tree_listing_is_capped(repo, monkeypatch):
    commit = _git(repo, "rev-parse", "HEAD")
    assert candidate_shadow._tree_entries(repo, commit)
    monkeypatch.setattr(cap, "MAX_GIT_READ", 10)
    with pytest.raises(GitCommandError):
        candidate_shadow._tree_entries(repo, commit)


def test_candidate_sys_path_blob_read_is_capped(repo):
    commit = _git(repo, "rev-parse", "HEAD")
    changed = {"pkg/small.py": "ADD", "pkg/big.py": "ADD"}
    assert candidate_sys_path._blob_signal(repo, commit, commit, "pkg/small.py", changed) == ""
    with pytest.raises(GitCommandError):
        candidate_sys_path._blob_signal(repo, commit, commit, "pkg/big.py", changed)


def test_tamper_scan_plugin_batch_read_is_capped(repo):
    small, big = _oid(repo, "pkg/small.py"), _oid(repo, "pkg/big.py")
    assert tamper_scan_plugin._batch(repo, [small]) == {small: b"x = 1\n"}
    with pytest.raises(GitCommandError):
        tamper_scan_plugin._batch(repo, [big])


def test_candidate_env_reads_the_test_source_through_the_cap(repo, monkeypatch):
    commit = _git(repo, "rev-parse", "HEAD")
    seen: list[tuple[str, ...]] = []
    real = candidate_env.read_git_bytes

    def spy(r, *args, **kwargs):
        seen.append(args)
        return real(r, *args, **kwargs)

    monkeypatch.setattr(candidate_env, "read_git_bytes", spy)
    task = SimpleNamespace(repo=repo, allowed_files=(), test_roots=("pkg/",))
    assert candidate_env._literal_test_writer(task, commit, ("/x", "pkg/small.py", 1)) is False
    assert seen and seen[0][0] == "show"
    with pytest.raises(GitCommandError):  # the same read, over the cap, is refused by the helper
        cap.read_git_bytes(repo, "show", f"{commit}:pkg/big.py")
