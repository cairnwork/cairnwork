"""Tracked symlinks at, above or under the test roots (#1000, #1289).

A directory symlink is one blob in ``git ls-tree -r``, but pytest follows it and collects the tests
behind the link path. Static listing never sees those paths, so every link under, at or above a test
root is doubt (fail closed): the paths it can reach stay untargetable. A link to a plain file is
doubt too: a candidate could retarget it to another file with the same node ids, and the base-side
listing cannot see that (#1279 review). A deleted link is not counted (``exclude``).
"""

from __future__ import annotations

from cairnwork.gitcmd import run_git_bytes

SYMLINK_MODE = b"120000"


def _reaches_root(path: str, roots: tuple[str, ...]) -> bool:
    """``path`` is at, above (a parent prefix of) or under one of ``roots``,
    case-folded (#1289)."""
    low = path.casefold()
    return any(low == r or low.startswith(f"{r}/") or r.startswith(f"{low}/") for r in roots)


def base_tree(task, exclude=frozenset()) -> tuple[bytes, list[str]]:
    """The base's tracked names (NUL-joined) and its symlink entries at, above or under the test
    roots, not counting the deleted ones."""
    raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    roots = tuple(root.casefold() for root in task.test_roots)
    names, links = [], []
    for entry in raw.split(b"\0"):
        meta, tab, name = entry.partition(b"\t")
        if not tab:
            continue
        names.append(name)
        if meta.partition(b" ")[0] == SYMLINK_MODE:
            path = name.decode("utf-8", "surrogateescape")
            if _reaches_root(path, roots) and path not in exclude:
                links.append(path)
    return b"\0".join(names), links
