"""#1767 (#986 AC7): every test file of the base tree, as targets for the fixture-name collect.

``base_fixture_names`` used to collect only the existing base files the test commit changes, so a
name that a base test in a sibling file requests was never recorded. These are all the base's test
files under the test roots that its own ``python_files`` selects and that the verifier's argv
grammar can name, so the collect sees the same items the base's own run would. Files are named one
by one (no directory argument), so the verifier's argv grammar and sandbox are unchanged. None when
the base cannot be read: the caller then keeps the narrow collect it always had.
"""

from __future__ import annotations

import posixpath

from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.issue_task import test_target
from cairnwork.p2p_collect import under_roots
from cairnwork.pytest_config import PytestConfigError, test_file_rule


def base_tree_targets(repo, base: str, roots: tuple[str, ...]) -> tuple[str, ...] | None:
    """Sorted, distinct base test files under ``roots`` (conftest files never), or None."""
    try:
        can_name, is_test = test_target(roots), test_file_rule(repo, base)
        raw = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", base, "--")
        paths = {
            p
            for p in under_roots(raw, roots)
            if posixpath.basename(p) != "conftest.py" and is_test(p) and can_name.fullmatch(p)
        }
    except (GitCommandError, PytestConfigError):
        return None
    return tuple(sorted(paths))
