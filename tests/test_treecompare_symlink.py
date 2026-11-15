"""#498: a symlink whose target is not UTF-8 is compared as bytes, never raised on."""

import os
import subprocess

import pytest

from cairnwork.treecompare import tree_differences


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_link(repo, target):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    os.symlink(target, repo / "link")
    _git(repo, "add", "link")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("other", [b"\xff", b"\xfe", b"plain"])
def test_different_target_is_changed_not_raised(tmp_path, other):
    repo, clone = tmp_path / "repo", tmp_path / "clone"
    repo.mkdir()
    clone.mkdir()
    commit = _commit_link(repo, b"\xff" if other != b"\xff" else b"plain")
    os.symlink(other, clone / "link")
    assert tree_differences(repo, commit, clone) == ["changed link"]


def test_identical_non_utf8_link_is_no_difference(tmp_path):
    repo, clone = tmp_path / "repo", tmp_path / "clone"
    repo.mkdir()
    clone.mkdir()
    commit = _commit_link(repo, b"\xff")
    os.symlink(b"\xff", clone / "link")
    assert tree_differences(repo, commit, clone) == []
