"""#924: which files a test-commit conftest can load through its imports.

The static check (conftest_check.py) reads the conftest's own nodes, and the differential base
probe (p2p_conftest.py) sees only base test outcomes. A module that the conftest imports and that
the test commit itself adds or changes is neither: its import-time code runs before every test,
can change the runner, and leaves every base outcome as it was. So a conftest change is refused
when one of its imports can load such a file, directly or through unchanged repository files in
the test commit's own top directories (where every test-commit path lives).

Resolution over-approximates pytest on purpose (refusing too much is the safe side):

* absolute imports are tried under the repository root, ``src/``, the verifier's import roots
  (the task's import_paths and the base's ``pythonpath``, r3) and every directory above the
  importing file (prepend mode puts a conftest's or test module's base directory first on
  ``sys.path``, ahead of the standard library), each dotted prefix as ``.py`` or a package's
  ``__init__.py``; ``from M import n`` also tries ``M.n``;
* relative imports are resolved from the importing file's directory;
* ``importlib.import_module("...")`` and ``__import__("...")`` with a literal name count as
  imports (a leading dot also relative to the file, a literal ``fromlist`` as submodules), and so
  do the literal names of a ``pytest_plugins`` assignment; every import counts, at any depth (a
  fixture body's too);
* the ``__init__.py`` of the conftest's directory and of each directory above it count too:
  pytest may import the conftest as a module of that package (r2, cw#948 cross-model review).

Whatever the roots, an absolute import whose top-level name is the stem of a changed ``.py``
file, or the name of a package directory above one, counts as loading it (#924 r4: a conftest may
put any directory on ``sys.path`` itself). A name built at run time, or an unchanged module
outside the test commit's top directories that imports a test-commit file, is not followed: that
is test author code, as in conftest_check.py.
Nor are base test files: a changed module that only a base test file imports also runs its
import-time code in the base run (a known residual, kept apart so that a test commit may still
update a shared test helper).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from posixpath import dirname, join, normpath

MAX_FILES = 500  # unchanged modules followed per conftest before the walk refuses


def reached_change(
    conftest: str,
    tree: ast.Module,
    changed: frozenset[str],
    files: frozenset[str],
    read,
    roots: Iterable[str] = (),
) -> str:
    """The first file in ``changed`` that ``conftest`` (parsed as ``tree``) can load, following
    imports through the unchanged ``files`` under the top directories of ``changed``; or a reason
    when there are too many to follow; '' when none. ``read(path)`` parses a file of the tree;
    ``roots`` are the verifier's import roots besides the defaults (its pythonpath)."""
    tops = {path.split("/", 1)[0] for path in changed}
    extra = [("" if n == "." else n) for n in (normpath(r).strip("/") for r in roots)]
    queue, seen = [(conftest, tree)], {conftest}
    for init in sorted(_inits(conftest) & files):  # the packages pytest imports the conftest in
        if init in changed:
            return init
        seen.add(init)
        queue.append((init, read(init)))
    by_name = _names(changed, files)
    while queue:
        here, parsed = queue.pop(0)
        if hit := _named(parsed, by_name):
            return hit
        for target in sorted(_targets(here, parsed, files, extra)):
            if target in changed:
                return target
            if target in seen or target.split("/", 1)[0] not in tops:
                continue
            if len(seen) > MAX_FILES:
                return f"has more than {MAX_FILES} modules to follow"
            seen.add(target)
            queue.append((target, read(target)))
    return ""


def _names(changed: frozenset[str], files: frozenset[str]) -> dict[str, str]:
    """#924 r4: top-level module names a changed ``.py`` file may be imported as, whatever the
    root: its stem, and the name of each package directory above it."""
    out: dict[str, str] = {}
    for path in sorted(p for p in changed if p.endswith(".py")):
        stem = path.rsplit("/", 1)[-1][:-3]
        if stem not in ("__init__", "conftest"):
            out.setdefault(stem, path)
        for directory in _above(dirname(path)):
            if join(directory, "__init__.py") in files:
                out.setdefault(directory.rsplit("/", 1)[-1], path)
    return out


def _named(tree: ast.Module, by_name: dict[str, str]) -> str:
    """The changed file an absolute import in ``tree`` names by its top-level name, or ''."""
    for level, names in _imports(tree):
        for name in names if not level else ():
            if hit := by_name.get(name.split(".", 1)[0]):
                return hit
    return ""


def _targets(here: str, tree: ast.Module, files: frozenset[str], extra: list[str]) -> set[str]:
    """The files of the tree that one import in ``tree`` (the file ``here``) may load."""
    roots = ["", "src", *extra, *_above(dirname(here))]
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
            name = _text(node.args[0])
            subs = [_text(x) for k in node.keywords if k.arg == "fromlist" for x in _items(k.value)]
            names = [name, *(f"{name}.{sub}" for sub in subs if sub)] if name else []
            dots = len(name) - len(name.lstrip("."))  # import_module(".x", package=...)
            yield 0, [n.lstrip(".") for n in names]
            if dots:
                yield dots, [n.lstrip(".") for n in names]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _plugins(node):  # pytest_plugins
            yield 0, [t for x in _items(node.value) if (t := _text(x))]


def _text(node: ast.expr | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _items(node: ast.expr | None) -> list[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    return [node] if node is not None else []


def _plugins(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets)


def _inits(conftest: str) -> set[str]:
    """The ``__init__.py`` of the conftest's directory and of each directory above it."""
    return {join(d, "__init__.py") for d in _above(dirname(conftest))}


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
