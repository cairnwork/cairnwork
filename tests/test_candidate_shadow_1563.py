"""Tests for Issue #1563: a same-named file elsewhere must not make a package module a shadow."""

from __future__ import annotations

import pytest
from conftest import commit_change, git

from cairnwork import candidate_shadow, candidate_sys_path
from cairnwork.workspace import CandidateRejected

REDIR = "import sys\nsys.path.insert(0, 'deep')\n"
SIBLINGS = {
    "pkg/__init__.py": "",
    "pkg/a/__init__.py": "",
    "pkg/a/core.py": "X = 1\n",
    "pkg/b/__init__.py": "",
    "pkg/b/core.py": "X = 1\n",
    "tests/test_t.py": "from pkg.a import core\n",
}


def _changed(repo, base, cand):
    return tuple(git(repo, "diff", "--name-only", base, cand).split())


def _scan(repo, base, cand):
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def _refused(repo, base, cand, name):
    with pytest.raises(CandidateRejected) as got:
        _scan(repo, base, cand)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert name in str(got.value)


def test_package_module_sharing_a_stem_with_a_directory_is_not_a_shadow(repo):
    commit_change(
        repo,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/term.py": "X = 1\n",
            "examples/term/__init__.py": "",
            "tests/test_t.py": "from pkg import term\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/pkg/term.py": "X = 2\n", "conftest.py": REDIR})
    _scan(repo, base, cand)


def test_package_module_with_same_name_in_sibling_subpackages_is_not_a_shadow(repo):
    commit_change(repo, SIBLINGS)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/a/core.py": "X = 2\n", "conftest.py": REDIR})
    _scan(repo, base, cand)
    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={"pkg/a/core.py": "MODIFY"}) is None
    )


def test_an_added_same_named_module_is_still_a_shadow(repo):
    commit_change(repo, SIBLINGS)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo, {"pkg/c/__init__.py": "", "pkg/c/core.py": "X = 3\n", "conftest.py": REDIR}
    )
    _refused(repo, base, cand, "pkg/c/core.py")


def test_a_top_level_importer_of_the_name_still_makes_it_a_shadow(repo):
    commit_change(repo, {**SIBLINGS, "tests/test_t.py": "import core\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/a/core.py": "X = 2\n", "conftest.py": REDIR})
    _refused(repo, base, cand, "pkg/a/core.py")


def test_same_name_outside_any_package_is_still_a_shadow(repo):
    commit_change(
        repo,
        {
            "deep/a/core.py": "X = 1\n",
            "deep/b/core.py": "X = 1\n",
            "tests/test_t.py": "import core\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"deep/a/core.py": "X = 2\n", "conftest.py": REDIR})
    _refused(repo, base, cand, "deep/a/core.py")


def test_a_stdlib_named_package_module_is_still_a_shadow(repo):
    commit_change(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/json.py": "X = 1\n",
            "other/json.py": "X = 1\n",
            "tests/test_t.py": "from pkg import json\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/json.py": "X = 2\n", "conftest.py": REDIR})
    _refused(repo, base, cand, "pkg/json.py")
