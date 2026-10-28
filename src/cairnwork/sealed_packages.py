"""Directories pytest's prepend import mode can never put on sys.path for a task (#382).

pytest (prepend or append mode, checked on 9.1.1, ``_pytest.pathlib.import_path``) puts on
sys.path the parent of a test or conftest module's top package, or the module's own directory
when that is not a package. Either way the directory it inserts has no ``__init__.py`` file or a
name that is not an identifier (``resolve_package_path`` stops at both). A directory that keeps
a regular ``__init__.py`` and an identifier name through the run is therefore never inserted,
and nothing in it is importable under a top-level name: the shadow gate need not treat it as an
import root (verifier_spec). The ``__init__.py`` stays when nothing can remove it:

- above a test root, no allowed path may be an ``__init__`` in that directory (the gate already
  refuses a new one, and a tracked one when deletions are allowed; here even a modifiable one
  counts as a change, so the answer never depends on the deletion check at snapshot);
- inside a test root, the candidate may not change anything, and the test commit may only add
  or modify regular files (deferred_tests.test_paths), so it can add an ``__init__.py`` but
  never remove one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git

_REGULAR = ("100644", "100755")


def sealed_packages(
    repo: Path | None,
    base_commit: str | None,
    roots: tuple[str, ...],
    above: tuple[str, ...],
    paths: tuple[str, ...],
) -> frozenset[str]:
    """The directories in ``above`` or under a test root that the base gives a regular file named
    exactly ``__init__.py`` (a case variant is not one on a case-sensitive host) and whose name is
    an identifier, minus those where an allowed path is (or is inside) an ``__init__`` of any
    spelling or suffix.
    Paths as git spells them. Empty without paths or when git cannot tell: every directory then
    counts as before."""
    if repo is None or base_commit is None or not paths:
        return frozenset()
    specs = (*(f"{d}/__init__.py" for d in above), *(f"{r}/" for r in roots))
    try:
        listing = run_git(repo, "ls-tree", "-r", "-z", base_commit, "--", *specs)
    except (GitCommandError, OSError, ValueError, subprocess.SubprocessError):
        return frozenset()
    touched = {
        "/".join(parts[:i]).casefold()
        for parts in (p.split("/") for p in paths)
        for i, part in enumerate(parts)
        if _is_init(part)
    }
    sealed = set()
    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, kind, _oid = meta.split()
        folder, _, leaf = path.rpartition("/")
        if (
            leaf == "__init__.py"
            and kind == "blob"
            and mode in _REGULAR
            and folder.rpartition("/")[2].isidentifier()
            and folder.casefold() not in touched
        ):
            sealed.add(folder)
    return frozenset(sealed)


def _is_init(leaf: str) -> bool:
    """Any ``__init__`` spelling a candidate could add or change: every case and suffix."""
    return leaf.split(".", 1)[0].casefold() == "__init__"
