"""#1767 (#986 AC7): every test file of the base tree, as targets for the fixture-name collect.

``base_fixture_names`` used to collect only the existing base files the test commit changes, so a
name that a base test in a sibling file requests was never recorded. These are all the base's test
files under the test roots that its own ``python_files`` selects and that the verifier's argv
grammar can name, so the collect sees the same items the base's own run would. Files are named one
by one (no directory argument), so the verifier's argv grammar and sandbox are unchanged. None when
the base cannot be read: the caller then keeps the narrow collect it always had.
"""

from __future__ import annotations

import bisect
import posixpath

from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.issue_task import test_target
from cairnwork.p2p_collect import under_roots
from cairnwork.pytest_config import PytestConfigError, test_file_rule

MAX_CONFTEST_DIRS = 600  # measured: 600 conftest directories collect-only in 4.7 s (1500: 10.7 s)


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


def conftest_dir_targets(repo, base: str, roots: tuple[str, ...]) -> tuple[str, ...] | None:
    """One base test file under each base conftest's directory (the least path), or None.

    A conftest loads when any test file below its directory is collected, so these few files make
    one collect-only run load every conftest's sys.path and plugin effects, without the whole tree.
    """
    try:
        raw = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", base, "--")
        dirs = {
            posixpath.dirname(p)
            for p in under_roots(raw, roots)
            if posixpath.basename(p) == "conftest.py"
        }
        tree = base_tree_targets(repo, base, roots)
    except (GitCommandError, PytestConfigError):
        return None
    return None if tree is None else pick_dir_targets(tree, sorted(dirs))


def pick_dir_targets(tree: tuple[str, ...], dirs) -> tuple[str, ...] | None:
    """The least file of the sorted ``tree`` below each conftest directory in ``dirs``.

    A directory with no test file below it picks nothing (no test loads it). None when there are
    more directories than MAX_CONFTEST_DIRS: the run would no longer be cheap.
    """
    dirs = set(dirs)
    if len(dirs) > MAX_CONFTEST_DIRS:
        return None
    picked = set()
    for d in dirs:
        at = bisect.bisect_left(tree, d + "/")
        if at < len(tree) and tree[at].startswith(d + "/"):
            picked.add(tree[at])
    return tuple(sorted(picked))
