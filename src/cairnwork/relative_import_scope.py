"""Which relative imports of a candidate name the package's own module (#1807, design C').

``from .types import x`` in ``pkg/validator.py`` is the sibling ``pkg/types.py``, not the stdlib
``types``, so the scan does not compare it with ``IMPORT_MODULES``. That holds only when the
judged trees prove it, by tree resolution alone (no file is read, only ``git ls-tree``):

- from the importing file's directory up to the top-level package, every directory has a tracked
  regular ``__init__.py`` (mode 100644/100755, no symlink) in BOTH the base and the candidate
  tree, so an ``__init__.py`` the same change adds, deletes or renames does not count;
- ``level`` stays inside the top-level package; each step of the module path but the last is a
  package, and the last is a regular ``.py`` file or a package, in the candidate tree;
- the top-level package is not named like a standard-library module and is not one of
  ``candidate_shadow._ALWAYS_SHADOW`` (``pytest``, ``_pytest``, ``pluggy``, ``conftest``, ``py``).

If either tree cannot be listed nothing resolves: the old rule applies. Anything else keeps the
module part compared with ``IMPORT_MODULES``.

Threat model: a relative import can only ever load ``top.sub.X``, never a top-level name. A
changed package-internal file can therefore shadow a stdlib module only if its directory is on
``sys.path`` or it runs as a script, and that comes from other signals, which still combine with
shadow files as before. Repointing ``__package__``/``__spec__``/``__name__`` can only make a
relative import not load the candidate file; reaching a real stdlib module through repointing is
a pre-existing hole that works without changing any shadow file (Issue #1816), so no word-list
taint check is made here. Residuals: that hole; base-only ``sys.path`` redirects (scripts,
``pytest.ini``, ``conftest.py``, ``.pth``); a top-level name is not compared with the packages
installed in the target environment.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from cairnwork.candidate_shadow import _ALWAYS_SHADOW  # pyright: ignore[reportPrivateUsage]
from cairnwork.gitcmd import GitCommandError, run_git

_REGULAR = ("100644", "100755")


class RelativeScope:
    """Answers, for one base/candidate pair, whether a relative import is the package's own."""

    def __init__(self, repo: Path, base: str, candidate: str) -> None:
        self._repo, self._base, self._candidate = repo, base, candidate
        self._trees: tuple[set[str], set[str]] | None = None  # tracked regular files

    def resolver(self, path: str) -> Callable[[int, str], bool]:
        """The check the scan calls for each relative import (level, module) of file ``path``."""
        return lambda level, module: self._resolves(path, level, module)

    def _files(self) -> tuple[set[str], set[str]]:
        if self._trees is None:
            try:
                self._trees = (self._list(self._base), self._list(self._candidate))
            except (GitCommandError, OSError, ValueError):
                self._trees = (set(), set())  # nothing resolves: scanned as before
        return self._trees

    def _list(self, commit: str) -> set[str]:
        listing = run_git(self._repo, "ls-tree", "-r", "-z", "--full-tree", commit)
        found: set[str] = set()
        for entry in filter(None, listing.split("\0")):
            meta, name = entry.split("\t", 1)
            mode, kind, _oid = meta.split()
            if kind == "blob" and mode in _REGULAR:
                found.add(name)
        return found

    def _resolves(self, path: str, level: int, module: str) -> bool:
        base, cand = self._files()
        dirs = path.split("/")[:-1]
        top = len(dirs)
        while top > 0 and (init := "/".join([*dirs[:top], "__init__.py"])) in base and init in cand:
            top -= 1
        if top == len(dirs) or level < 1:
            return False  # no package here
        if dirs[top] in sys.stdlib_module_names or dirs[top] in _ALWAYS_SHADOW:
            return False
        start = len(dirs) - (level - 1)
        if start <= top:
            return False  # level climbs out of the top-level package
        cur, parts = dirs[:start], module.split(".")
        for part in parts[:-1]:
            cur = [*cur, part]
            if "/".join([*cur, "__init__.py"]) not in cand:
                return False
        last = "/".join([*cur, parts[-1]])
        return f"{last}.py" in cand or f"{last}/__init__.py" in cand
