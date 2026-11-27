"""Direct behaviour checks for deferred overlay edge cases."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cairnwork.deferred_overlay import TestCommitRejected, overlay_tree, stray_deletions


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    for name, body in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _reject(
    repo: Path,
    head: str,
    tests: str,
    tmp_path: Path,
    expected: str,
    *,
    paths: tuple[str, ...] = (),
    deleted: tuple[str, ...] = (),
) -> None:
    with pytest.raises(TestCommitRejected) as caught:
        overlay_tree(repo, head, tests, paths, tmp_path / "private-index", deleted)
    assert caught.value.code == "OVERLAY_UNSUPPORTED_ENTRY"
    assert str(caught.value) == f"OVERLAY_UNSUPPORTED_ENTRY: {expected}"


@pytest.mark.parametrize("path", ["", "a//b", "a/./b", "a/../b", ".GiT/config"])
def test_rejects_invalid_path_components(tmp_path: Path, path: str) -> None:
    repo, head = _repo(tmp_path, {"ok.py": "base\n"})
    _reject(repo, head, head, tmp_path, repr(path), paths=(path,))


def test_rejects_path_named_in_both_sets_with_exact_detail(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path, {"a.py": "base\n"})
    _reject(
        repo,
        head,
        head,
        tmp_path,
        "a path both laid and deleted",
        paths=("a.py",),
        deleted=("a.py",),
    )


def test_dash_prefixed_added_and_deleted_paths_are_operands(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"-x.py": "old\n", "keep.py": "keep\n"})
    _git(repo, "rm", "-q", "--", "-x.py")
    _git(repo, "checkout", "-q", "--detach", base)
    (repo / "-x.py").write_text("new\n")
    tests = _commit(repo, "test version")
    tree = overlay_tree(repo, base, tests, ("-x.py",), tmp_path / "add-index")
    assert _git(repo, "show", f"{tree}:-x.py") == "new"
    _git(repo, "rm", "-q", "--", "-x.py")
    gone = _commit(repo, "test commit without the file")
    deleted_tree = overlay_tree(repo, base, gone, (), tmp_path / "delete-index", ("-x.py",))
    assert "-x.py" not in _git(repo, "ls-tree", "-r", "--name-only", deleted_tree).splitlines()


@pytest.mark.parametrize(
    ("entry", "mode"), [("symlink", "120000"), ("gitlink", "160000"), ("directory", "040000")]
)
def test_refuses_non_regular_entries_with_mode(tmp_path: Path, entry: str, mode: str) -> None:
    repo, base = _repo(tmp_path, {"target": "target\n"})
    if entry == "symlink":
        (repo / "link").symlink_to("target")
        _git(repo, "add", "link")
        path = "link"
    elif entry == "gitlink":
        object_id = _git(repo, "rev-parse", "HEAD")
        _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{object_id},sub")
        path = "sub"
        _git(repo, "commit", "-q", "-m", entry)
        tests = _git(repo, "rev-parse", "HEAD")
        _reject(repo, base, tests, tmp_path, f"{mode} {path!r}", paths=(path,))
        return
    else:
        (repo / "dir").mkdir()
        (repo / "dir/child.py").write_text("child\n")
        _git(repo, "add", "dir/child.py")
        path = "dir"
    tests = _commit(repo, entry)
    _reject(repo, base, tests, tmp_path, f"{mode} {path!r}", paths=(path,))


def test_missing_test_path_reports_dash_mode(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path, {"base.py": "base\n"})
    _reject(repo, head, head, tmp_path, "- 'missing.py'", paths=("missing.py",))


def test_regular_executable_mode_is_preserved(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"run.sh": "base\n"})
    (repo / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "run.sh").chmod(0o755)
    tests = _commit(repo, "executable test file")
    tree = overlay_tree(repo, base, tests, ("run.sh",), tmp_path / "index")
    assert _git(repo, "ls-tree", tree, "--", "run.sh").split()[0] == "100755"


def test_deleted_path_must_be_absent_as_file_but_directory_swap_is_allowed(
    tmp_path: Path,
) -> None:
    repo, base = _repo(tmp_path, {"helper": "file\n"})
    (repo / "helper").unlink()
    (repo / "helper").mkdir()
    (repo / "helper/test_a.py").write_text("def test_a(): pass\n")
    tests = _commit(repo, "replace file with directory")
    tree = overlay_tree(repo, base, tests, (), tmp_path / "tree-index", ("helper",))
    assert _git(repo, "ls-tree", "-r", "--name-only", tree) == ""  # the file is removed

    _git(repo, "checkout", "-q", "--detach", base)
    _reject(repo, base, base, tmp_path, "deleted path in tests 'helper'", deleted=("helper",))


def test_unknown_test_commit_is_reported_as_overlay_failure_with_detail(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path, {"base.py": "base\n"})
    with pytest.raises(TestCommitRejected) as caught:
        overlay_tree(repo, head, "not-a-commit", ("base.py",), tmp_path / "index")
    assert caught.value.code == "TEST_OVERLAY_FAILED"
    message = str(caught.value)
    assert message.startswith("TEST_OVERLAY_FAILED: GIT_COMMAND_FAILED: git ls-tree failed:")
    assert caught.value.has_detail


def test_stray_deletions_keeps_rename_like_delete_and_add_and_subtracts_declared(
    tmp_path: Path,
) -> None:
    repo, base = _repo(tmp_path, {"old.py": "same\n", "declared.py": "delete\n"})
    (repo / "old.py").unlink()
    (repo / "new.py").write_text("same\n")
    (repo / "declared.py").unlink()
    judged = _commit(repo, "delete and add same content")
    assert stray_deletions(repo, base, judged, ()) == ["declared.py", "old.py"]
    assert stray_deletions(repo, base, judged, ("declared.py",)) == ["old.py"]


@pytest.mark.parametrize("name", ["tests/dataX", "tab\tname.py"])
def test_added_path_with_trailing_x_or_tab_is_laid_over(tmp_path: Path, name: str) -> None:
    repo, base = _repo(tmp_path, {"base.py": "base\n"})
    (repo / "tests").mkdir()
    (repo / name).write_text("body\n")
    tests = _commit(repo, "test file with an unusual name")
    tree = overlay_tree(repo, base, tests, (name,), tmp_path / "index")
    assert _git(repo, "ls-tree", "-z", tree, "--", name).split("\t")[0].split()[:2] == [
        "100644",
        "blob",
    ]


# Equivalent mutants (no observable behaviour differs), not killed on purpose:
# line 95: strip(None) / dropped -z / strip("XX\0XX") only touch whitespace, the name or the
#   NUL, never the second field (the kind) that split()[1:2] reads.
# line 103: the kind and blob defaults are unread because name != path raises first.
# line 104: `and` / name-only variants need ls-tree to return a different name for an exact
#   pathspec; the empty-segment guard on line 90 removes the only such shape ("dir/").
# line 124: --no-renames: plumbing diff-tree detects no renames unless -M is given.
