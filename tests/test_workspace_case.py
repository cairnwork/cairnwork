"""#428: a base tree with paths that differ only in case, on a case-insensitive filesystem,
is refused with that reason rather than the generic "not a clean checkout"."""

import os

import pytest
from conftest import git

import cairnwork.workspace as workspace
from cairnwork.workspace import (
    WorkspaceError,
    _case_collisions,
    _collision_detail,
    _unexplained,
    create_attempt_clone,
)


def _folds_case(directory):
    probe = directory / "CaseProbe"
    probe.write_text("")
    try:
        return os.path.exists(directory / "caseprobe")
    finally:
        probe.unlink()


def _commit_with(repo, *names):
    """Commit base plus names (same content) built in the index only: the files never exist."""
    git(repo, "config", "core.ignorecase", "false")
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    for name in names:
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    tree = git(repo, "write-tree")
    return git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "case")


def test_case_colliding_files_are_named(repo, tmp_path):
    base = _commit_with(repo, "docs/X.html", "docs/x.html", "::warning::A", "::WARNING::a")
    if not _folds_case(tmp_path):
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")  # nothing collides
        return
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    message = str(info.value)
    assert f"is not a clean checkout of {base}: base tree has case-colliding paths" in message
    assert "other difference" not in message  # the collision explains every difference
    assert "on a case-insensitive filesystem" in message
    assert "('docs/X.html', 'docs/x.html')" in message
    assert "('::WARNING::a', '::warning::A')" in message  # quoted, never a bare '::' line


def test_case_colliding_directories_are_named(repo, tmp_path):
    base = _commit_with(repo, "Docs/a.html", "docs/b.html")
    if not _folds_case(tmp_path):
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
        return
    with pytest.raises(WorkspaceError, match=r"case-colliding paths \('Docs', 'docs'\)"):
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")


def test_a_base_without_collisions_still_clones(repo, tmp_path):
    base = _commit_with(repo, "docs/X.html", "docs/y.html")
    made = create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    assert git(made.path, "rev-parse", "HEAD") == base


def test_another_difference_is_reported_beside_the_collision(repo, tmp_path, monkeypatch):
    """A collision must not hide another cause (here a leftover the check also found)."""
    base = _commit_with(repo, "docs/X.html", "docs/x.html")
    real = workspace.tree_differences
    monkeypatch.setattr(
        workspace, "tree_differences", lambda *a: [*real(*a), "extra ::warning::left.txt"]
    )
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    message = str(info.value)
    assert "is not a clean checkout of" in message
    if _folds_case(tmp_path):
        assert "case-colliding" in message
        assert "1 other difference(s): 'extra ::warning::left.txt'" in message


# The attribution itself, independent of the host's filesystem.
GROUPS = _case_collisions(["Docs/a.html", "docs/b.html", "lib/X.py", "lib/x.py", "y.py"])


def test_case_collisions_group_files_and_directories():
    assert GROUPS == [["Docs", "docs"], ["lib/X.py", "lib/x.py"]]
    assert _case_collisions(["a/X", "b/x", "c"]) == []


def test_only_differences_under_a_collision_are_explained():
    explained = ["missing docs/b.html", "extra Docs/b.html", "changed lib/X.py", "missing lib/x.py"]
    explained.append("extra directory DOCS/sub")
    assert _unexplained(explained, GROUPS) == []
    other = [
        "extra notes.txt",
        "changed y.py",
        "missing lib/z.py",
        "unreadable docs/b.html",
        "non-empty submodule docs/m",
    ]
    assert _unexplained(explained + other, GROUPS) == other


def test_the_detail_caps_the_groups_and_quotes_other_differences():
    groups = [[f"{c}.txt", f"{c.upper()}.txt"] for c in "abcd"]
    detail = _collision_detail(groups, ["extra ::x", "extra b", "extra c", "extra d"])
    assert "('a.txt', 'A.txt'), ('b.txt', 'B.txt'), ('c.txt', 'C.txt') and 1 more" in detail
    assert detail.endswith("; 4 other difference(s): 'extra ::x', 'extra b', 'extra c'")
    assert "other" not in _collision_detail(groups[:1], [])
