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


def _mod_a(repo, files, extra=None):
    commit_change(repo, {**SIBLINGS, **files})
    base = git(repo, "rev-parse", "HEAD")
    return base, commit_change(
        repo, {"pkg/a/core.py": "X = 2\n", **(extra or {"conftest.py": REDIR})}
    )


@pytest.mark.parametrize(
    "test_src",
    [
        "from importlib import import_module as load\nload('core')\n",
        "import importlib\nload = importlib.import_module\nload('core')\n",
        "load = __import__\nload('core')\n",
        "import importlib\nname = 'core'\nimportlib.import_module(name)\n",
        "import importlib\nimportlib.import_module(*['co' + 're'])\n",
    ],
)
def test_a_dynamic_or_aliased_import_keeps_the_duplicate_refusal(repo, test_src):
    base, cand = _mod_a(repo, {"tests/test_d.py": test_src})
    _refused(repo, base, cand, "pkg/a/core.py")


def test_a_dynamic_import_in_a_non_test_file_keeps_the_duplicate_refusal(repo):
    src = "from importlib import import_module as load\nload('core')\n"
    base, cand = _mod_a(repo, {"pkg/loader.py": src})
    _refused(repo, base, cand, "pkg/a/core.py")


def test_a_package_init_with_a_twin_is_still_a_shadow(repo):
    files = {
        "pkg/a/sub/__init__.py": "",
        "pkg/b/sub/__init__.py": "",
        "tests/test_t.py": "from pkg.a import sub\n",
    }
    commit_change(repo, {**SIBLINGS, **files})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/a/sub/__init__.py": "X = 2\n", "conftest.py": REDIR})
    _refused(repo, base, cand, "pkg/a/sub/__init__.py")


def test_a_module_in_a_directory_without_init_with_a_twin_is_still_a_shadow(repo):
    files = {"loose/core.py": "X = 1\n", "tests/test_t.py": "from pkg.a import core\n"}
    commit_change(repo, {**SIBLINGS, **files})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"loose/core.py": "X = 2\n", "conftest.py": REDIR})
    _refused(repo, base, cand, "loose/core.py")


def test_a_bare_importer_in_another_directory_keeps_the_refusal(repo):
    base, cand = _mod_a(repo, {"tools/use.py": "import core\n"})
    _refused(repo, base, cand, "pkg/a/core.py")


def test_a_module_in_an_importable_root_without_init_with_a_twin_is_still_a_shadow(repo):
    files = {
        "src/core.py": "X = 1\n",
        "lib/core.py": "X = 1\n",
        "tests/test_t.py": "from lib import core\n",
    }
    commit_change(repo, files)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/core.py": "X = 2\n", "conftest.py": REDIR})
    _refused(repo, base, cand, "src/core.py")


def test_importers_in_its_own_and_another_directory_keep_the_refusal(repo):
    files = {"pkg/a/use.py": "import core\n", "tools/use.py": "import core\n"}
    base, cand = _mod_a(repo, files)
    _refused(repo, base, cand, "pkg/a/core.py")
