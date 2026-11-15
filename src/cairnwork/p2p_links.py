"""Tracked symlinks under the test roots (#1000).

A directory symlink is one blob in ``git ls-tree -r``, but pytest follows it and collects the tests
behind the link path. Static listing never sees those paths, so a link under a test root is doubt
(fail closed): the paths it can reach stay untargetable. A link to a plain tracked file is no
doubt: pytest collects that one file, which the listing already names.
"""

from __future__ import annotations

import posixpath

from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.p2p_collect import under_roots

SYMLINK_MODE = b"120000"
PLAIN_MODES = (b"100644", b"100755")


def base_tree(task, exclude=frozenset()) -> tuple[bytes, list[str]]:
    """The base's tracked names (NUL-joined) and its symlink entries under the test roots that
    are not a link to a plain tracked file, not counting the deleted ones."""
    raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    names, modes, links = [], {}, {}
    for entry in raw.split(b"\0"):
        meta, tab, name = entry.partition(b"\t")
        if not tab:
            continue
        names.append(name)
        mode, _, rest = meta.partition(b" ")
        modes[name] = mode
        if mode == SYMLINK_MODE:
            links[name] = rest.split(b" ")[-1]  # the blob id git printed
    doubt = [n for n, blob in links.items() if not _plain_file_link(task, n, blob, modes)]
    found = under_roots(b"\0".join(doubt), task.test_roots)
    return b"\0".join(names), [p for p in found if p not in exclude]


def _plain_file_link(task, name: bytes, blob: bytes, modes: dict) -> bool:
    """True when the link's target (read from the base) is a tracked regular file in the tree."""
    try:
        target = run_git_bytes(task.repo, "cat-file", "blob", blob.decode("ascii"))
    except GitCommandError:  # unreadable: doubt
        return False
    # Only a name `ls-tree` printed is in `modes`: an empty, NUL, absolute or `..`-escaping target
    # joins to a path that is never one of them, so it is doubt without a separate check.
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    return modes.get(joined) in PLAIN_MODES
