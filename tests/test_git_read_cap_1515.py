"""#1515 family: the verifier's reads of candidate git objects are held to a byte cap."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cairnwork.git_read_cap as cap
from cairnwork import candidate_env, candidate_shadow, candidate_sys_path, tamper_scan_plugin
from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan_budget import ScanBudget

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


def test_shadow_scan_of_sources_summing_past_the_cap_is_not_refused(repo, monkeypatch):
    for n in range(40):
        (repo / "pkg" / f"m{n}.py").write_text(f"v{n} = {n}\n" + "#" * 150 + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "many")
    commit = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(cap, "MAX_GIT_READ", 4000)  # each blob fits; all 40 together do not
    (repo / "pkg" / "big.py").unlink()
    _git(repo, "commit", "-q", "-am", "drop big")
    commit = _git(repo, "rev-parse", "HEAD")
    assert candidate_shadow.find_shadow_file(repo, commit, changed={"pkg/m1.py": "MODIFY"}) is None


def test_shadow_scan_refuses_one_source_blob_past_the_cap(repo):
    commit = _git(repo, "rev-parse", "HEAD")  # pkg/big.py is over the cap
    with pytest.raises(GitCommandError):
        candidate_shadow.find_shadow_file(repo, commit, changed={"pkg/small.py": "MODIFY"})


def test_candidate_env_refuses_an_oversized_control_source(repo, monkeypatch):
    monkeypatch.setattr(cap, "MAX_GIT_READ", 1 << 20)
    body = "open('/etc/denied', 'w')\n" + "#" * 6000 + "\n"
    (repo / "pkg" / "test_x.py").write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "t")
    commit = _git(repo, "rev-parse", "HEAD")
    task = SimpleNamespace(repo=repo, allowed_files=(), test_roots=("pkg/",))
    record = ("/etc/denied", "pkg/test_x.py", 1)
    assert candidate_env._literal_test_writer(task, commit, record) is True  # control: readable
    monkeypatch.setattr(cap, "MAX_GIT_READ", SMALL_CAP)
    assert candidate_env._literal_test_writer(task, commit, record) is False


def test_file_rules_diff_listing_is_capped(repo, monkeypatch):
    base = _git(repo, "rev-parse", "HEAD")
    for n in range(30):
        (repo / f"{'d' * 60}{n}.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "names")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(cap, "MAX_GIT_READ", 1 << 20)
    tamper_scan_plugin.file_rules(
        repo, base, head, ScanBudget(), python=sys.executable
    )  # control: the listing reads fine
    monkeypatch.setattr(cap, "MAX_GIT_READ", 1000)
    with pytest.raises(GitCommandError):
        tamper_scan_plugin.file_rules(repo, base, head, ScanBudget(), python=sys.executable)


def test_sys_path_base_side_read_is_capped(repo):
    base = _git(repo, "rev-parse", "HEAD")  # pkg/big.py is over the cap in the base
    (repo / "pkg" / "big.py").write_text("y = 1\n")
    _git(repo, "commit", "-q", "-am", "shrink")
    head = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(GitCommandError):
        candidate_sys_path._blob_signal(repo, base, head, "pkg/big.py", {"pkg/big.py": "MODIFY"})
