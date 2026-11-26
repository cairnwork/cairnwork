"""Which relative imports of a candidate name the package's own module (#1807, design C').

``from .types import x`` in ``pkg/validator.py`` is the sibling ``pkg/types.py``, not the stdlib
``types``, so the scan does not compare it with ``IMPORT_MODULES``. That holds only when the
judged trees prove it (``git ls-tree``, plus a read of the chain's ``__init__.py`` files):

- from the importing file's directory up to the top-level package, every directory has a tracked
  regular ``__init__.py`` (mode 100644/100755, no symlink) in BOTH the base and the candidate
  tree, so an ``__init__.py`` the same change adds, deletes or renames does not count;
- ``level`` stays inside the top-level package; each step of the module path but the last is a
  package, and the last is a regular ``.py`` file or a package, in the candidate tree;
- the top-level package is not named like a standard-library module and is not one of
  ``candidate_shadow._ALWAYS_SHADOW`` (``pytest``, ``_pytest``, ``pluggy``, ``conftest``, ``py``);
- no ``__init__.py`` of the chain (either tree) can rewrite the package search path
  (``__path__``, ``extend_path``, ``setattr``/``globals()`` writes; unreadable or unparseable
  counts as yes), since a moved path loads a module from elsewhere.

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

import ast
import importlib.util
import pkgutil
import re
import sys
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path

from cairnwork.candidate_shadow import _ALWAYS_SHADOW  # pyright: ignore[reportPrivateUsage]
from cairnwork.git_read_cap import read_git_bytes
from cairnwork.gitcmd import GitCommandError
from cairnwork.sys_path_names import IMPORT_MODULES

_REGULAR = ("100644", "100755")
_BARRED = {n.casefold() for n in (*sys.stdlib_module_names, *_ALWAYS_SHADOW)}


_CONFIG = {
    "conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "noxfile.py",
    "makefile", "sitecustomize.py", "usercustomize.py", ".env",
}  # fmt: skip
_CONFIG_SUFFIX = (".pth", ".yml", ".yaml")
_PATH_WORDS = ("pythonpath", "sys.path", ".pth", "addsitedir", "syspath", "site")
_MOVERS = {
    "__path__", "__package__", "extend_path", "submodule_search_locations", "__dict__",
    "__spec__", "setattr", "globals", "vars", "exec", "eval", "__import__",
}  # fmt: skip


def _moves_search_path(data: bytes) -> bool:
    """Whether an ``__init__.py`` can rewrite its package's search path: it names ``__path__``,
    ``extend_path``, ``__spec__``, ``__dict__`` or a name-built write (``setattr``, ``globals``,
    ``vars``, ``exec``, ``eval``, ``__import__``), as a name, attribute, import or string value.
    A docstring or comment does not count; a file that cannot be parsed does."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return True
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _MOVERS:
            return True
        if isinstance(node, ast.alias) and node.name.split(".")[-1] in _MOVERS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _MOVERS:
            return True
        if isinstance(node, ast.Constant) and node.value in _MOVERS and id(node) not in docs:
            return True
    return False


_CONTEXT = {"__package__", "__spec__", "__name__", "__path__", "__loader__", "__file__"}
_DYNAMIC = {"setattr", "globals", "vars", "exec", "eval", "__import__", "__dict__"}


def _rebinds_context(data: bytes) -> bool:
    """Whether a changed file can rewrite a module's package context: it binds, deletes or names
    as a string one of ``_CONTEXT`` (reading ``__name__`` or ``__package__`` is ordinary code), or
    calls a dynamic writer. A file that cannot be parsed counts as yes."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return True
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
    for n in ast.walk(tree):
        bound = (
            (isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load) and n.id)
            or (isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Load) and n.attr)
            or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name)
            or (isinstance(n, ast.arg) and n.arg)
            or (isinstance(n, ast.ExceptHandler) and n.name)
            or (isinstance(n, ast.alias) and (n.asname or n.name))
        )
        if bound in _CONTEXT:
            return True
        if isinstance(n, ast.Name) and n.id in _DYNAMIC:
            return True
        if isinstance(n, ast.Attribute) and n.attr in _DYNAMIC:
            return True
        if isinstance(n, ast.Constant) and n.value in _CONTEXT and id(n) not in docs:
            return True
    return False


def _relative_targets(path: str, data: bytes) -> set[str]:
    """Tree paths the relative imports of one file name (``a/b.py`` and ``a/b/__init__.py``);
    ``from . import x`` names ``x``. A file that cannot be parsed names none."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return set()
    dirs = path.split("/")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level < 1:
            continue
        start = len(dirs) - (node.level - 1)
        if start < 0:
            continue
        parts = node.module.split(".") if node.module else []
        names = [parts] if node.module else []
        names += [[*parts, a.name] for a in node.names]  # ``from . import x`` may name a module
        for dotted in names:
            for i in range(1, len(dotted) + 1):
                stem = "/".join([*dirs[:start], *dotted[:i]])
                found |= {f"{stem}.py", f"{stem}/__init__.py"}
    return found


@cache
def _machinery_leaves() -> frozenset[str]:
    """Names of stdlib sub-modules (``unittest.mock``, ``asyncio.subprocess``) that are also
    IMPORT_MODULES names: a package whose context is repointed at a stdlib package would load
    that real module through ``from .mock import x``, so such a first step is not exempt."""
    bare = {name for name in IMPORT_MODULES if "." not in name} - {"types"}
    found: set[str] = set()
    for top in sorted(sys.stdlib_module_names):
        try:
            spec = importlib.util.find_spec(top)
        except (ImportError, ValueError, AttributeError):
            continue
        for info in pkgutil.iter_modules(spec.submodule_search_locations or [] if spec else []):
            found.add(info.name)
    return frozenset(found & bare)


class RelativeScope:
    """Answers, for one base/candidate pair, whether a relative import is the package's own."""

    def __init__(self, repo: Path, base: str, candidate: str) -> None:
        self._repo, self._base, self._candidate = repo, base, candidate
        self._trees: tuple[set[str], set[str]] | None = None  # tracked regular files
        self._moves: dict[str, bool] = {}

    def resolver(self, path: str) -> Callable[[int, str], bool]:
        """The check the scan calls for each relative import (level, module) of file ``path``."""
        return lambda level, module: self._resolves(path, level, module)

    def _init_moves(self, init: str) -> bool:
        """Whether either version of the chain's ``init`` can rewrite the search path (unreadable:
        yes). Read through ``git show`` with the capped reader, one file per chain step."""
        if init not in self._moves:
            try:
                self._moves[init] = any(
                    _moves_search_path(read_git_bytes(self._repo, "show", f"{commit}:{init}"))
                    for commit in (self._base, self._candidate)
                )
            except (GitCommandError, OSError, ValueError):
                self._moves[init] = True
        return self._moves[init]

    def own_shadows(self, changed: Iterable[str]) -> frozenset[str]:
        """The changed files a changed relative import names as its package's own module, when the
        package is proven (as for ``resolver``), no changed file rewrites its package or search
        path, and no config text near a path word names the package directory. Anything unread,
        too large or undecodable keeps the file a shadow candidate."""
        paths = [p for p in changed if p.endswith(".py")]
        try:
            blobs = {p: read_git_bytes(self._repo, "show", f"{self._candidate}:{p}") for p in paths}
        except (GitCommandError, OSError, ValueError):
            return frozenset()
        if any(_rebinds_context(data) for data in blobs.values()):
            return frozenset()
        named = {t for p, data in blobs.items() for t in _relative_targets(p, data)}
        return frozenset(p for p in paths if p in named and self._own(p))

    def _own(self, path: str) -> bool:
        base, cand = self._files()
        dirs = path.split("/")[:-1]
        top = len(dirs)
        while top > 0 and (init := "/".join([*dirs[:top], "__init__.py"])) in base and init in cand:
            top -= 1
        if top == len(dirs) or path not in cand or dirs[top].casefold() in _BARRED:
            return False
        chain = ("/".join([*dirs[:i], "__init__.py"]) for i in range(top + 1, len(dirs) + 1))
        return not any(self._init_moves(i) for i in chain) and not self._dir_on_path(dirs)

    def _dir_on_path(self, dirs: list[str]) -> bool:
        """Whether a config text names the package directory next to a path word (unreadable,
        too large, undecodable or too many files: yes). Only ever makes the check stricter."""
        name = re.compile(rf"(?<!\w){re.escape(dirs[-1])}(?!\w)|{re.escape('/'.join(dirs))}", re.I)
        seen = 0
        for commit, tree in ((self._base, self._files()[0]), (self._candidate, self._files()[1])):
            for f in sorted(tree):
                leaf = f.rsplit("/", 1)[-1].casefold()
                if not (leaf in _CONFIG or leaf.endswith(_CONFIG_SUFFIX)):
                    continue
                seen += 1
                if seen > 200:
                    return True
                try:
                    text = read_git_bytes(self._repo, "show", f"{commit}:{f}").decode("utf-8")
                except (GitCommandError, OSError, ValueError):
                    return True
                if leaf.endswith(".pth") or (
                    any(w in text.casefold() for w in _PATH_WORDS) and name.search(text)
                ):
                    return True
        return False

    def _files(self) -> tuple[set[str], set[str]]:
        if self._trees is None:
            try:
                self._trees = (self._list(self._base), self._list(self._candidate))
            except (GitCommandError, OSError, ValueError):
                self._trees = (set(), set())  # nothing resolves: scanned as before
        return self._trees

    def _list(self, commit: str) -> set[str]:
        listing = read_git_bytes(self._repo, "ls-tree", "-r", "-z", "--full-tree", commit).decode(
            "utf-8", "surrogateescape"
        )
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
        if top == len(dirs) or level < 1 or not module:
            return False  # no package here
        if dirs[top].casefold() in _BARRED:
            return False
        chain = ("/".join([*dirs[:i], "__init__.py"]) for i in range(top + 1, len(dirs) + 1))
        if any(self._init_moves(init) for init in chain):
            return False  # an __init__ can move the search path: not provably the sibling
        start = len(dirs) - (level - 1)
        if start <= top:
            return False  # level climbs out of the top-level package
        cur, parts = dirs[:start], module.split(".")
        if parts[0] in _machinery_leaves():
            return False  # may be a repointed stdlib package: the old comparison applies
        for part in parts[:-1]:
            cur = [*cur, part]
            if "/".join([*cur, "__init__.py"]) not in cand:
                return False
        last = "/".join([*cur, parts[-1]])
        return f"{last}.py" in cand or f"{last}/__init__.py" in cand
