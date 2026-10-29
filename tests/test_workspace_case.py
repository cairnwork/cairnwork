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
    assert "; differences: 'missing " in message  # every difference is still listed
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


def _with_extra(monkeypatch, *extra):
    real = workspace.tree_differences
    monkeypatch.setattr(workspace, "tree_differences", lambda *a: [*real(*a), *extra])


def test_a_difference_under_a_colliding_directory_is_still_listed(repo, tmp_path, monkeypatch):
    """The collision only adds to the error: an unrelated leftover, even inside a colliding
    directory, is always shown. _folds_case is forced so this runs on every filesystem."""
    base = _commit_with(repo, "Docs/a.html", "docs/b.html")
    monkeypatch.setattr(workspace, "_folds_case", lambda clone: True)
    _with_extra(monkeypatch, "extra directory DOCS/sub", "extra ::warning::left.txt")
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    message = str(info.value)
    assert f"is not a clean checkout of {base}: base tree has case-colliding paths" in message
    assert "'extra directory DOCS/sub'" in message
    assert "'extra ::warning::left.txt'" in message


def test_a_head_other_than_base_keeps_the_generic_error(repo, tmp_path, monkeypatch):
    base = _commit_with(repo, "docs/X.html", "docs/x.html")
    monkeypatch.setattr(workspace, "_folds_case", lambda clone: True)
    real = workspace.run_git
    monkeypatch.setattr(
        workspace,
        "run_git",
        lambda path, *a, **k: "0" * 40 if a == ("rev-parse", "HEAD") else real(path, *a, **k),
    )
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    assert str(info.value).endswith(f"is not a clean checkout of {base}")


# The message itself, independent of the host's filesystem.


def test_case_collisions_group_files_and_directories():
    names = ["Docs/a.html", "docs/b.html", "lib/X.py", "lib/x.py", "y.py"]
    assert _case_collisions(names) == [["Docs", "docs"], ["lib/X.py", "lib/x.py"]]
    assert _case_collisions(["a/X", "b/x", "c"]) == []


def test_the_detail_caps_groups_and_lists_every_kind_of_difference():
    groups = [[f"{c}.txt", f"{c.upper()}.txt"] for c in "abcd"]
    differences = [f"extra ::{n}" for n in range(7)]
    detail = _collision_detail(groups, differences)
    assert "('a.txt', 'A.txt'), ('b.txt', 'B.txt'), ('c.txt', 'C.txt') and 1 more" in detail
    shown = ", ".join(repr(d) for d in differences[:5])
    assert detail.endswith(f"; differences: {shown} and 2 more")
    assert _collision_detail(groups[:1], ["missing a.txt"]).endswith("differences: 'missing a.txt'")
