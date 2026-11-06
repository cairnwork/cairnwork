"""#924: which files a test-commit conftest can load through its imports.

The static check (conftest_check.py) reads the conftest's own nodes, and the differential base
probe (p2p_conftest.py) sees only base test outcomes. A module that the conftest imports and that
the test commit itself adds or changes is neither: its import-time code runs before every test,
can change the runner, and leaves every base outcome as it was. So a conftest change is refused
when one of its imports can load such a file, directly or through unchanged repository files in
the test commit's own top directories (where every test-commit path lives).

Resolution over-approximates pytest on purpose (refusing too much is the safe side):

* absolute imports are tried under the repository root, ``src/`` and every directory above the
  importing file (prepend mode puts a conftest's or test module's base directory first on
  ``sys.path``, ahead of the standard library), each dotted prefix as ``.py`` or a package's
  ``__init__.py``; ``from M import n`` also tries ``M.n``;
* relative imports are resolved from the importing file's directory;
* ``importlib.import_module("...")`` and ``__import__("...")`` with a literal name count as
  imports; every import statement counts, at any depth (a fixture body's too).

A name built at run time, or an unchanged module outside the test commit's top directories that
imports a test-commit file, is not followed: that is test author code, as in conftest_check.py.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from posixpath import dirname, join

MAX_FILES = 500  # unchanged modules followed per conftest before the walk refuses


def reached_change(
    conftest: str, tree: ast.Module, changed: frozenset[str], files: frozenset[str], read
) -> str:
    """The first file in ``changed`` that ``conftest`` (parsed as ``tree``) can load, following
    imports through the unchanged ``files`` under the top directories of ``changed``; or a reason
    when there are too many to follow; '' when none. ``read(path)`` parses a file of the tree."""
    tops = {path.split("/", 1)[0] for path in changed}
    queue, seen = [(conftest, tree)], {conftest}
    while queue:
        here, parsed = queue.pop(0)
        for target in sorted(_targets(here, parsed, files)):
            if target in changed:
                return target
            if target in seen or target.split("/", 1)[0] not in tops:
                continue
            if len(seen) > MAX_FILES:
                return f"has more than {MAX_FILES} modules to follow"
            seen.add(target)
            queue.append((target, read(target)))
    return ""


def _targets(here: str, tree: ast.Module, files: frozenset[str]) -> set[str]:
    """The files of the tree that one import in ``tree`` (the file ``here``) may load."""
    roots = ["", "src", *_above(dirname(here))]
    found: set[str] = set()
    for level, names in _imports(tree):
        if level:
            base = dirname(here)
            for _ in range(level - 1):
                base = dirname(base)
            starts = [base]  # the empty module name tries the package's own __init__.py
        else:
            starts = roots
        for start in starts:
            for name in names:
                found |= _module_files(start, name.split(".") if name else []) & files
    return found


def _imports(tree: ast.Module) -> Iterator[tuple[int, Iterable[str]]]:
    """(level, dotted names it may load) per import in ``tree``, at any depth."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield 0, [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield node.level, [module, *(f"{module}.{a.name}".lstrip(".") for a in node.names)]
        elif isinstance(node, ast.Call) and _dynamic(node.func) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield 0, [first.value]


def _dynamic(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == "import_module"
    return isinstance(func, ast.Name) and func.id in ("__import__", "import_module")


def _module_files(start: str, parts: list[str]) -> set[str]:
    """Each dotted prefix of ``parts`` under ``start`` as a module file or a package init."""
    out = {join(start, "__init__.py")} if start else set()
    for k in range(1, len(parts) + 1):
        stem = join(start, *parts[:k]) if start else join(*parts[:k])
        out |= {f"{stem}.py", join(stem, "__init__.py")}
    return {p.lstrip("/") for p in out}


def _above(directory: str) -> list[str]:
    """``directory`` and each directory above it, short of the repository root."""
    out = []
    while directory:
        out.append(directory)
        directory = dirname(directory)
    return out
