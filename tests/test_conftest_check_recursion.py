"""#1022: a conftest nested too deeply for the parser, or for a walk of its tree, is refused
fail-closed as unparsable input; the error never escapes the check and the file is never read
as empty."""

import pytest
from conftest import commit_change, git
from conftest_scenarios import BASE_CONFTEST, FIXTURE
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)

from cairnwork.conftest_check import check_conftests
from cairnwork.deferred_overlay import TestCommitRejected


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _commit(project, files):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files)
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


def _deep(depth):
    return "\nLIMIT = " + "1 + " * depth + "1\n"


def _code(project, base, commit):
    with pytest.raises(TestCommitRejected) as caught:
        check_conftests(project, base, commit, ["tests/conftest.py"])
    return caught.value.code


def test_a_conftest_nested_past_the_parser_limit_is_refused_as_unparsable(project):
    base, commit = _commit(project, {"tests/conftest.py": BASE_CONFTEST + _deep(200_000)})
    assert _code(project, base, commit) == "TEST_COMMIT_UNPARSABLE"


def test_a_deep_base_conftest_that_parses_but_cannot_be_walked_is_refused_as_unparsable(project):
    # Every supported Python either refuses to parse it or parses it and overflows the walk.
    deep = BASE_CONFTEST + _deep(5_000)
    base, _ = _commit(project, {"tests/conftest.py": deep})
    commit_change(project, {"tests/conftest.py": deep})
    base = git(project, "rev-parse", "HEAD")
    _, commit = _commit(project, {"tests/conftest.py": deep + FIXTURE})
    assert _code(project, base, commit) == "TEST_COMMIT_UNPARSABLE"


@pytest.mark.parametrize("depth", [50, 3_000])
def test_a_deep_expression_the_parser_accepts_is_still_refused(project, depth):
    base, commit = _commit(project, {"tests/conftest.py": BASE_CONFTEST + _deep(depth)})
    # Judged on its content (not a literal) or refused as unparsable, never an escaping error.
    assert _code(project, base, commit) in ("TEST_COMMIT_CONFTEST_STATIC", "TEST_COMMIT_UNPARSABLE")
