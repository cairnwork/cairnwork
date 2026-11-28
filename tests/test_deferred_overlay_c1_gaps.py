"""C1 regression checks for deferred overlay survivor mutants."""

import pytest
from conftest import commit_change, git

import cairnwork.deferred_overlay as deferred_overlay
from cairnwork.deferred_overlay import TestCommitRejected as Rejected
from cairnwork.deferred_overlay import overlay_tree


@pytest.fixture
def overlay_history(repo):
    base = commit_change(repo, {"keep.txt": "base\n", "gone.txt": "old\n"})
    head = commit_change(repo, {"candidate.txt": "candidate\n"})
    git(repo, "checkout", "-q", "--detach", base)
    tests = commit_change(
        repo,
        {"tests/test_ok.py": "def test_ok(): pass\n", "XX--XX": "unrelated\n"},
        delete=("gone.txt",),
    )
    git(repo, "checkout", "-q", "-f", "main")
    return base, head, tests


@pytest.mark.parametrize("path", ["tests//x.py", "tests/./x.py", "tests/../x.py"])
def test_overlay_rejects_empty_dot_and_parent_components(monkeypatch, tmp_path, path):
    calls = []

    def fake_run_git(_repo, *args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(deferred_overlay, "run_git", fake_run_git)
    with pytest.raises(Rejected) as caught:
        overlay_tree(tmp_path, "head", "tests", (path,), tmp_path / "index")
    assert caught.value.code == "OVERLAY_UNSUPPORTED_ENTRY", caught.value
    assert calls == [("read-tree", "head")]


def test_overlay_removes_a_deleted_path_despite_unrelated_entries(repo, overlay_history, tmp_path):
    _base, head, tests = overlay_history
    tree = overlay_tree(repo, head, tests, ("tests/test_ok.py",), tmp_path / "index", ("gone.txt",))
    assert "gone.txt" not in git(repo, "ls-tree", "-r", "--name-only", tree).splitlines()


def test_deleted_path_replaced_by_directory_does_not_leave_file(repo, tmp_path):
    base = commit_change(repo, {"tests/helper": "file\n"})
    git(repo, "checkout", "-q", "--detach", base)
    git(repo, "rm", "-q", "tests/helper")
    tests = commit_change(repo, {"tests/helper/test_x.py": "x\n"})
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(repo, base, tests, (), tmp_path / "index", ("tests/helper",))
    assert "tests/helper" not in git(repo, "ls-tree", "-r", "--name-only", tree).splitlines()


def test_overlay_preserves_a_path_containing_a_tab(repo, tmp_path):
    path = "tests/test\tcase.py"
    base = commit_change(repo, {path: "old\n"})
    git(repo, "checkout", "-q", "--detach", base)
    tests = commit_change(repo, {path: "new\n"})
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(repo, base, tests, (path,), tmp_path / "index")
    assert git(repo, "show", f"{tree}:{path}") == "new"


@pytest.mark.parametrize("path", ["-file.py", "--delete-me"])
def test_overlay_handles_paths_starting_with_a_dash(repo, tmp_path, path):
    base = commit_change(repo, {path: "old\n"})
    git(repo, "checkout", "-q", "--detach", base)
    tests = commit_change(repo, {path: "new\n"})
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(repo, base, tests, (path,), tmp_path / "index")
    assert git(repo, "show", f"{tree}:{path}") == "new"


def test_deleted_path_starting_with_a_dash_is_removed(repo, tmp_path):
    path = "-removed.txt"
    base = commit_change(repo, {path: "old\n", "keep.txt": "keep\n"})
    git(repo, "checkout", "-q", "--detach", base)
    git(repo, "rm", "-q", "--", path)
    git(repo, "commit", "-q", "-m", "delete dash path")
    tests = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(repo, base, tests, (), tmp_path / "index", (path,))
    assert path not in git(repo, "ls-tree", "-r", "--name-only", tree).splitlines()


def test_extra_pathspec_match_does_not_change_the_selected_overlay_entry(repo, tmp_path):
    base = commit_change(repo, {"z.py": "base\n"})
    git(repo, "checkout", "-q", "--detach", base)
    tests = commit_change(repo, {"z.py": "selected\n", "XX--XX": "unrelated\n"})
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(repo, base, tests, ("z.py",), tmp_path / "index")
    assert git(repo, "show", f"{tree}:z.py") == "selected"


def test_overlay_rejects_a_name_that_rstrip_would_change(repo, tmp_path):
    path = "tests/testX"
    base = commit_change(repo, {path: "base\n"})
    git(repo, "checkout", "-q", "--detach", base)
    tests = commit_change(repo, {path: "new\n"})
    git(repo, "checkout", "-q", "-f", "main")
    tree = overlay_tree(repo, base, tests, (path,), tmp_path / "index")
    assert git(repo, "show", f"{tree}:{path}") == "new"


def test_overlay_refuses_a_symlink_with_the_stable_code(repo, tmp_path):
    base = commit_change(repo, {"tests/regular.py": "x\n"})
    git(repo, "checkout", "-q", "--detach", base)
    (repo / "tests/link").symlink_to("regular.py")
    git(repo, "add", "tests/link")
    git(repo, "commit", "-q", "-m", "symlink entry")
    tests = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-f", "main")
    with pytest.raises(Rejected) as caught:
        overlay_tree(repo, base, tests, ("tests/link",), tmp_path / "index")
    assert caught.value.code == "OVERLAY_UNSUPPORTED_ENTRY"


def test_overlay_reports_git_plumbing_failure_with_its_stable_code(repo, tmp_path):
    with pytest.raises(Rejected) as caught:
        overlay_tree(repo, "missing-object", "also-missing", (), tmp_path / "index")
    assert caught.value.code == "TEST_OVERLAY_FAILED"
