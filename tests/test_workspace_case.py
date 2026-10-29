"""#428: a base tree with paths that differ only in case, on a case-insensitive filesystem,
is refused with that reason rather than the generic "not a clean checkout"."""

import os

import pytest
from conftest import git

from cairnwork.workspace import WorkspaceError, create_attempt_clone


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
    assert "base tree has case-colliding paths" in message
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
