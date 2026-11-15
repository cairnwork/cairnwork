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


def _dir_link_repo(tmp_path):
    """A commit with d/f and a link `dlink` (target b'\\xff'); a clone whose `dlink` points at a
    real directory, so os.walk lists it among the directories (the directory-symlink branch)."""
    repo, clone = tmp_path / "repo", tmp_path / "clone"
    for path in (repo, clone):
        (path / "d").mkdir(parents=True)
        (path / "d" / "f").write_text("x")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    os.symlink(b"\xff", repo / "dlink")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    os.symlink("d", clone / "dlink")
    return repo, clone, _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("target", "expected"),
    [(b"\xff", []), (b"\xfe", ["changed dlink"]), (b"plain", ["changed dlink"])],
)
def test_directory_symlink_with_a_non_utf8_target(tmp_path, monkeypatch, target, expected):
    # macOS cannot create a non-UTF-8 name that is also a directory link, so os.readlink is faked
    # to return what Linux returns for such a target: a str with surrogate escapes.
    repo, clone, commit = _dir_link_repo(tmp_path)
    real = os.readlink
    fake = lambda p, *a, **k: os.fsdecode(target) if str(p).endswith("dlink") else real(p, *a, **k)  # noqa: E731
    monkeypatch.setattr(os, "readlink", fake)
    assert tree_differences(repo, commit, clone) == expected
