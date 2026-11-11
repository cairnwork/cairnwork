"""Tests for Issue #1148: modified unique module as a shadow (#1111 residual)."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_shadow, candidate_sys_path
from cairnwork.workspace import CandidateRejected

REDIR = "import sys\nsys.path.insert(0, 'deep')\n"


def _changed(repo, base, cand):
    """Every changed path as the candidate's allowed files."""
    return tuple(git(repo, "diff", "--name-only", base, cand).split())


def test_modified_unique_module_outside_an_import_root_is_a_shadow(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/foo.py": "X = 2\n", "conftest.py": REDIR})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "deep/foo.py" in str(got.value)


def test_find_shadow_file_names_the_modified_unique_module(repo):
    cand = commit_change(repo, {"deep/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={"deep/foo.py": "MODIFY"})
        == "deep/foo.py"
    )


def test_importer_from_the_test_commit_counts(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/foo.py": "X = 2\n", "conftest.py": REDIR})
    git(repo, "checkout", "-q", "--detach", base)
    tc = commit_change(repo, {"tests/test_t.py": "import foo\n"})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand), test_commit=tc
        )
    assert "deep/foo.py" in str(got.value)


def test_same_directory_unchanged_importer_is_not_a_shadow(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n", "deep/test_foo.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/foo.py": "X = 2\n", "conftest.py": REDIR})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_package_member_is_not_a_shadow(repo):
    commit_change(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/foo.py": "X = 1\n",
            "tests/test_t.py": "from pkg import foo\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/foo.py": "X = 2\n", "conftest.py": REDIR})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_lib_layout_module_is_not_a_shadow(repo):
    commit_change(repo, {"lib/foo.py": "X = 1\n", "tests/test_t.py": "import foo\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"lib/foo.py": "X = 2\n", "conftest.py": REDIR})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_changed_importer_in_the_same_directory_does_not_exempt(repo):
    commit_change(repo, {"deep/foo.py": "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"deep/foo.py": "X = 2\n", "deep/test_foo.py": "import foo\n", "conftest.py": REDIR},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "deep/foo.py" in str(got.value)
