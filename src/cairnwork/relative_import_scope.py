"""Which relative imports of a candidate name the package's own module (#1807).

``from .types import x`` in ``pkg/validator.py`` is the sibling ``pkg/types.py``, not the stdlib
``types``, so the scan does not compare it with ``IMPORT_MODULES``. That holds only when all of:

- the target is in the judged tree: walking from the importing file's directory, every level up
  to the top-level package has a tracked regular ``__init__.py``, ``level`` stays inside it, and
  each step of the module path is a tracked ``.py`` file or a tracked package;
- the top-level package is not named like a standard-library module (a file at
  ``unittest/x.py`` that says ``from .mock import patch`` runs against the real ``unittest``
  under ``--import-mode=importlib``);
- nothing can repoint the resolution: no changed Python file and no file of the top-level package
  touches ``__package__``, ``__spec__`` or ``__name__`` other than as a plain read of ``__name__``
  (``__package__ = "unittest"`` in a star-imported sibling, ``v.__package__ = ...`` through a
  cycle, a script run with ``__name__`` set). A file that cannot be read or parsed counts, and
  so does a package whose files together exceed ``MAX_TOTAL_BYTES`` (one file ``MAX_FILE_BYTES``)
  or ``MAX_FILES``: the answer is then "not exempt", never a partial read.

Anything else keeps the module part compared with ``IMPORT_MODULES``, as before. A comment that
names the words does not count. The taint read covers every tracked file of the top-level
package (unchanged ones too) and the changed files anywhere, for the words as names or literal
strings. Not read: unchanged code outside the package, and an unchanged base file that sets those
attributes through encoded bytes (``exec`` of a bytes constant): the base is the target
repository's own, and the threat model is an untrusted candidate patch on a trusted base. A top
level package named like a third-party module (``requests``) is exempt like any other (#1807).
"""

from __future__ import annotations

import ast
import sys
import warnings
from collections.abc import Callable, Iterable
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.sys_path_names import names_resolution_attrs
from cairnwork.sys_path_scan import MAX_FILES, _read_blobs  # pyright: ignore[reportPrivateUsage]

MAX_FILE_BYTES = 2 << 20  # one source file; a larger one is not read
MAX_TOTAL_BYTES = 32 << 20  # all files read for one package's taint
_REGULAR = ("100644", "100755")
_SOURCE = (".py", ".pyw")


class RelativeScope:
    """Answers, for one judged commit, whether a relative import resolves to the package's own."""

    def __init__(self, repo: Path, commit: str, changed: Iterable[str]) -> None:
        self._repo, self._commit = repo, commit
        self._changed = [p for p in changed if p.endswith(_SOURCE)]
        self._tree: dict[str, tuple[str, int]] | None = None  # tracked regular file -> (blob, size)
        self._tainted: dict[str, bool] = {}

    def resolver(self, path: str) -> Callable[[int, str], bool]:
        """The check the scan calls for each relative import (level, module) of file ``path``."""
        return lambda level, module: self._resolves(path, level, module)

    def _files(self) -> dict[str, tuple[str, int]]:
        if self._tree is None:
            self._tree = {}
            try:
                listing = run_git(
                    self._repo, "ls-tree", "-r", "-l", "-z", "--full-tree", self._commit
                )
                for entry in filter(None, listing.split("\0")):
                    meta, path = entry.split("\t", 1)
                    mode, kind, oid, size = meta.split()
                    if kind == "blob" and mode in _REGULAR and size.isdigit():
                        self._tree[path] = (oid, int(size))
            except (GitCommandError, OSError, ValueError):
                self._tree = {}  # nothing resolves: every relative import is scanned as before
        return self._tree

    def _resolves(self, path: str, level: int, module: str) -> bool:
        tree, dirs = self._files(), path.split("/")[:-1]
        top = len(dirs)
        while top > 0 and "/".join([*dirs[:top], "__init__.py"]) in tree:
            top -= 1
        if top == len(dirs) or dirs[top] in sys.stdlib_module_names or level < 1:
            return False  # no package here, or the top-level package is named like the stdlib
        base = len(dirs) - (level - 1)
        if base <= top:
            return False
        cur, parts = dirs[:base], module.split(".")
        for part in parts[:-1]:
            cur = [*cur, part]
            if "/".join([*cur, "__init__.py"]) not in tree:
                return False
        last = "/".join([*cur, parts[-1]])
        found = f"{last}.py" in tree or f"{last}/__init__.py" in tree
        return found and not self._taint("/".join(dirs[: top + 1]))

    def _taint(self, package: str) -> bool:
        """Whether a changed file or a file of ``package`` can repoint a relative import."""
        if package not in self._tainted:
            tree = self._files()
            inside = [p for p in tree if p.startswith(package + "/") and p.endswith(_SOURCE)]
            paths = sorted({*inside, *(p for p in self._changed if p in tree)})
            sizes = [tree[p][1] for p in paths]
            too_big = len(paths) > MAX_FILES or sum(sizes) > MAX_TOTAL_BYTES
            too_big = too_big or any(size > MAX_FILE_BYTES for size in sizes)
            self._tainted[package] = too_big or _repoints(self._repo, [tree[p][0] for p in paths])
        return self._tainted[package]


def _repoints(repo: Path, oids: list[str]) -> bool:
    blobs = _read_blobs(repo, list(dict.fromkeys(oids)))
    if blobs is None:
        return True
    for data in blobs:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(data)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return True
        if any(names_resolution_attrs(node) for node in ast.walk(tree)):
            return True
    return False
