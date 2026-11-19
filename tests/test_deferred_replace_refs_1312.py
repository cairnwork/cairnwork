"""The local test-commit shortcut is safe with Git replacement refs disabled."""

from conftest import commit_change, git

from cairnwork.deferred_tests import ensure_test_commit
from cairnwork.gitcmd import run_git

ORIGINAL = "def test_original():\n    assert 1 == 0\n"
REPLACEMENT = "def test_replacement():\n    assert 1 == 1\n"


def test_local_test_commit_ignores_replace_ref_and_needs_no_origin(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "original")
    commit_change(repo, {"tests/test_regression.py": ORIGINAL}, message="original tests")
    original = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-q", "-b", "replacement", base)
    commit_change(repo, {"tests/test_regression.py": REPLACEMENT}, message="replacement tests")
    replacement = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "original")
    git(repo, "replace", original, replacement)

    # This test repository has no verifier-owned origin; local presence is sufficient.
    ensure_test_commit(repo, original)
    assert run_git(repo, "show", f"{original}:tests/test_regression.py") == ORIGINAL
