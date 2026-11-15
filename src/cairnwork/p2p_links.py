"""Tracked symlinks under the test roots (#1000).

A directory symlink is one blob in ``git ls-tree -r``, but pytest follows it and collects the tests
behind the link path. Static listing never sees those paths, so a link under a test root is doubt
(fail closed): the paths it can reach stay untargetable.
"""

from __future__ import annotations

from cairnwork.gitcmd import run_git_bytes
from cairnwork.p2p_collect import under_roots

SYMLINK_MODE = b"120000"


def base_tree(task, exclude=frozenset()) -> tuple[bytes, list[str]]:
    """The base's tracked names (NUL-joined) and its symlink entries under the test roots, not
    counting the deleted ones."""
    raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    names, links = [], []
    for entry in raw.split(b"\0"):
        meta, tab, name = entry.partition(b"\t")
        if tab:
            names.append(name)
            if meta.split(b" ", 1)[0] == SYMLINK_MODE:
                links.append(name)
    found = under_roots(b"\0".join(links), task.test_roots)
    return b"\0".join(names), [p for p in found if p not in exclude]
