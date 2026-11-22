"""#986 step 5a: collected fixture names are only ever added to the taken set (advisory input)."""

import ast

import pytest
from conftest import commit_change, git

from cairnwork.conftest_check import check_conftests
from cairnwork.conftest_names import taken_names
from cairnwork.deferred_overlay import TestCommitRejected

BASE = "import pytest\n\n\n@pytest.fixture\ndef check():\n    return 1\n"
BASE_TEST = "def test_old(check, tmp_path):\n    pass\n"
PLUGIN_NAME = "plugin_made"  # a name only a plugin provides: static reading cannot see it
ADD = BASE + f"\n\n@pytest.fixture\ndef {PLUGIN_NAME}():\n    return 2\n"


def _base(repo):
    commit_change(repo, {"tests/conftest.py": BASE, "tests/test_old.py": BASE_TEST})
    return git(repo, "rev-parse", "HEAD")


def _taken(repo, base, **kwargs):
    return taken_names(repo, base, "tests/conftest.py", ast.parse(BASE), **kwargs)


def test_a_collected_only_name_is_refused(repo):
    base = _base(repo)
    commit = commit_change(repo, {"tests/conftest.py": ADD}, message="tests")
    check_conftests(repo, base, commit, ["tests/conftest.py"])  # static reading accepts it
    with pytest.raises(TestCommitRejected):
        check_conftests(
            repo, base, commit, ["tests/conftest.py"], collected=frozenset({PLUGIN_NAME})
        )


@pytest.mark.parametrize("empty", [None, frozenset()])
def test_none_and_empty_give_the_old_result_exactly(repo, empty):
    base = _base(repo)
    assert _taken(repo, base, collected=empty) == _taken(repo, base)
    commit = commit_change(repo, {"tests/conftest.py": ADD}, message="tests")
    check_conftests(repo, base, commit, ["tests/conftest.py"], collected=empty)


def test_collected_names_only_add(repo):
    base = _base(repo)
    plain_bound, plain_taken = _taken(repo, base)
    bound_names, taken = _taken(repo, base, collected=frozenset({PLUGIN_NAME, "check"}))
    assert bound_names == plain_bound  # the conftest's own bound names never change
    assert taken == plain_taken | {PLUGIN_NAME, "check"}
    assert plain_taken <= taken


def test_an_unrelated_collected_name_does_not_change_other_verdicts(repo):
    base = _base(repo)
    commit = commit_change(repo, {"tests/conftest.py": ADD}, message="tests")
    check_conftests(repo, base, commit, ["tests/conftest.py"], collected=frozenset({"other_name"}))
