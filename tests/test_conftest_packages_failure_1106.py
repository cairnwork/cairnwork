"""#1106 item 3: a failed own-package listing narrows the conftest import allowlist."""

import pytest
from conftest import commit_change, git
from conftest_scenarios import BASE_CONFTEST  # noqa: F401
from conftest_scenarios import project as _project  # noqa: F401, F811

import cairnwork.conftest_check as cc
import cairnwork.deferred_tests as dt


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def test_package_enumeration_failure_over_refuses_own_package_import(project, monkeypatch):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, {"tests/conftest.py": BASE_CONFTEST + "\nimport calc\n"})
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    original = cc.run_git

    def fail_tree_listing(repo, *args, **kwargs):
        if args[:1] == ("ls-tree",):
            raise cc.GitCommandError("forced package enumeration failure")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(cc, "run_git", fail_tree_listing)
    with pytest.raises(dt.TestCommitRejected, match="outside the standard library"):
        cc.check_conftests(project, base, commit, ("tests/conftest.py",))
