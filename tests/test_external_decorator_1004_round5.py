"""#1004 last round: any name that can rewrite a module anywhere voids every stdlib module, and a
symlink named ``.py`` (base or candidate) turns the relaxation off."""

import ast
import os

import pytest
from conftest import commit_change, git
from test_external_decorator_1004_failclosed import CAND, FACTORY, HELPER, _base, _blocked

import cairnwork.deferred_tests as dt
from cairnwork.fixture_forwarding import voided_modules

WIDE = [
    "vars(functools)['wraps'] = 1",
    "import sys\nsys.modules['func' + 'tools'] = object()",
    "import sys\nsys.modules[name] = object()",
    "__builtins__['exec']('1')",
    "import builtins\nbuiltins.x = 1",
    "globals()['x'] = 1",
    "locals()['x'] = 1",
    "import functools\nsetattr(functools, 'a', 1)",
    "import functools\nfunctools.__dict__['a'] = 1",
]


@pytest.mark.parametrize("body", WIDE)
def test_a_wide_name_anywhere_voids_every_module(body):
    assert "*" in voided_modules({"o": ast.parse(body)})


@pytest.mark.parametrize("body", WIDE)
def test_a_wide_name_in_an_unimported_base_file_blocks(repo, body):
    assert _blocked(repo, HELPER, {**FACTORY, "tests/other/p.py": body + "\n"})


def test_a_symlink_named_py_in_the_base_turns_the_relaxation_off(repo):
    commit_change(repo, {**_base(HELPER), **FACTORY, "tests/other/p.txt": "x = 1\n"})
    os.symlink("p.txt", repo / "tests/other/link.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    base = git(repo, "rev-parse", "HEAD")
    commit = commit_change(repo, CAND, message="tests")
    with pytest.raises(dt.TestCommitRejected):
        from cairnwork.conftest_check import check_conftests

        check_conftests(repo, base, commit, list(CAND))


def test_a_symlink_named_py_in_the_candidate_turns_the_relaxation_off(repo):
    commit_change(repo, {**_base(HELPER), **FACTORY, "tests/other/p.txt": "x = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    commit_change(repo, CAND)
    os.symlink("p.txt", repo / "tests/other/link.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    commit = git(repo, "rev-parse", "HEAD")
    from cairnwork.conftest_check import check_conftests

    with pytest.raises(dt.TestCommitRejected):
        check_conftests(repo, base, commit, [*CAND, "tests/other/link.py"])
